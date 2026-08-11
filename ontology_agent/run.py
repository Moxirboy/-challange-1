"""CLI orchestration over N CSVs, in order.

Spec: DESIGN.md §10.

    python -m ontology_agent.run \\
      --ontology fixtures/seed_ontology.json \\
      --csv fixtures/1_vendors.csv fixtures/2_product_catalog.csv fixtures/3_crm_export.csv \\
      --out out/ [--answers answers.json] [--escalation-budget 2]
      [--approve auto|interactive|none] [--no-llm] [--cache-dir .cache]

Flow: load ontology -> startup hygiene audit -> for each CSV in order:
profile -> prefilter -> subject type -> retrieve+decide per column -> gates
-> batched escalation -> patch -> validate -> emit patch + report -> apply
-> rebuild index -> next CSV. Finally write out/final_ontology.json and
out/run_summary.json.

Everything printed to stdout is the transcript; it is also teed to
out/transcript.md. Never prints the API key, .env contents, or raw request
headers -- only the resolved base_url/model are ever echoed.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time

from .decide import DEFAULT_ESCALATION_BUDGET, Decision, apply_escalation_budget, decide_column, decide_subject_type, decision_to_patch_op
from .escalate import ask, build_question, classify_source, incorporate, load_answers_file
from .models import AddTypeOp, FlagOntologyIssueOp, Ontology
from .ontology import audit, build_cards
from .patch import apply as patch_apply
from .patch import validate
from .profiler import profile_csv
from .report import build_report, project_rows, write_report
from .retrieval import ConceptIndex

try:
    from .llm import LLM, Embedder, resolve_embed_config, resolve_llm_config
except ImportError:  # pragma: no cover - only hit if llm.py is broken/missing
    LLM = None  # type: ignore[assignment,misc]
    Embedder = None  # type: ignore[assignment,misc]
    resolve_llm_config = None  # type: ignore[assignment,misc]
    resolve_embed_config = None  # type: ignore[assignment,misc]


# --------------------------------------------------------------------------
# Transcript tee.
# --------------------------------------------------------------------------


class _Tee:
    """Duplicates every write to stdout AND the transcript file. Installed
    as sys.stdout for the duration of the run so every `print()` anywhere
    in the package (including escalate.py's question blocks) lands in
    both places, without threading a writer object through every call."""

    def __init__(self, *streams) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _banner(title: str) -> None:
    print()
    print("=" * 78)
    print(f" {title}")
    print("=" * 78)


# --------------------------------------------------------------------------
# LLM/Embedder construction. Uses llm.py's own `from_env()` classmethods and
# `resolve_llm_config()`/`resolve_embed_config()` (its public env-resolution
# surface per DESIGN.md §4) rather than duplicating that precedence chain
# here -- run.py's only extra responsibility is refusing to start an LLM run
# with no key at all, with a message pointing at --no-llm.
# --------------------------------------------------------------------------


def _build_llm_clients(cache_dir: str) -> tuple["LLM", "Embedder", str, str]:
    """Returns (llm, embedder, llm_model_label, embed_model_label). The two
    label strings are only for the transcript banner -- LLM/Embedder keep
    their resolved base_url/model private (no key-carrying `__repr__`), so
    the labels are read from the same public resolve_*_config() calls used
    to build the clients rather than off the client objects."""
    if LLM is None or Embedder is None or resolve_llm_config is None or resolve_embed_config is None:
        raise SystemExit("ontology_agent.llm is missing or failed to import; use --no-llm to run without it.")
    llm_base_url, api_key, llm_model = resolve_llm_config()
    if not api_key:
        raise SystemExit(
            "No API key found (checked CHALLENGE_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY, OPENAI_API_KEY "
            "in env and .env). Pass --no-llm to run the deterministic pipeline without one."
        )
    _, _, embed_model = resolve_embed_config()
    llm = LLM.from_env(cache_dir)
    embedder = Embedder.from_env(cache_dir)
    return llm, embedder, f"{llm_model} @ {llm_base_url}", embed_model


# --------------------------------------------------------------------------
# Output naming.
# --------------------------------------------------------------------------


def _report_stem(csv_path: str, index: int) -> str:
    """out/<n>_<name> per §9. The fixture filenames already carry their own
    `1_`/`2_`/`3_` prefix, so naively prefixing again would double up
    (`1_1_vendors`); strip any existing leading `<digits>_` from the stem
    first, then apply the CLI's own 1-based index."""
    raw_stem = os.path.splitext(os.path.basename(csv_path))[0]
    core = re.sub(r"^\d+_", "", raw_stem)
    return f"{index}_{core}"


# --------------------------------------------------------------------------
# Argument parsing.
# --------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ontology_agent.run",
        description="Ontology retrieval & expansion harness: map N CSVs onto an ontology, in order.",
    )
    parser.add_argument("--ontology", required=True, help="path to the seed ontology JSON")
    parser.add_argument("--csv", nargs="+", required=True, help="CSV files to ingest, in order")
    parser.add_argument("--out", required=True, help="output directory for patches/reports/transcript")
    parser.add_argument("--answers", default=None, help="optional JSON file of {question_id: answer}")
    parser.add_argument("--escalation-budget", type=int, default=DEFAULT_ESCALATION_BUDGET, help="max escalations kept per CSV")
    # Default is the conservative "none" (emit-only): the tool never mutates
    # the ontology unless the caller opts in, even though the demo command
    # in DESIGN.md's verification step passes --approve auto explicitly.
    parser.add_argument("--approve", choices=["auto", "interactive", "none"], default="none", help="patch application mode")
    parser.add_argument("--no-llm", action="store_true", help="run the deterministic heuristic decider; no network, no API key")
    parser.add_argument("--cache-dir", default=".cache", help="LLM/embedding disk cache directory")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------
# Main run.
# --------------------------------------------------------------------------


def _run(args: argparse.Namespace) -> None:
    start = time.perf_counter()

    _banner("ONTOLOGY AGENT RUN")
    print(f"mode:            {'no-llm (heuristic decider)' if args.no_llm else 'llm'}")
    print(f"approve:         {args.approve}")
    print(f"escalation budget per CSV: {args.escalation_budget}")
    print(f"ontology:        {args.ontology}")
    print(f"csvs:            {args.csv}")
    print(f"out:             {args.out}")

    llm = None
    embedder = None
    if not args.no_llm:
        llm, embedder, llm_label, embed_model_label = _build_llm_clients(args.cache_dir)
        # Model/base_url are not secrets; the key itself is never printed.
        print(f"LLM:             {llm_label}")
        print(f"Embed model:     {embed_model_label}")

    ontology = Ontology.from_file(args.ontology)

    _banner("STARTUP HYGIENE AUDIT")
    startup_flags: list[FlagOntologyIssueOp] = audit(ontology, embedder=embedder)
    if startup_flags:
        for flag in startup_flags:
            print(f"  [{flag.severity}] {flag.target}: {flag.issue}")
    else:
        print("  (no issues found)")

    index = ConceptIndex(build_cards(ontology, embedder=embedder), embedder=embedder)

    csv_summaries: list[dict] = []

    for i, csv_path in enumerate(args.csv, start=1):
        csv_name = os.path.basename(csv_path)
        _banner(f"CSV {i}/{len(args.csv)}: {csv_name}")

        print("-- profiling --")
        profile = profile_csv(csv_path)
        print(f"  rows={profile.row_count} columns={len(profile.columns)}")

        print("-- subject type --")
        subject = decide_subject_type(csv_name, profile, index, llm)
        print(f"  {subject.subject_type} (is_new={subject.is_new}, confidence={subject.confidence:.2f})")
        print(f"  rationale: {subject.rationale}")

        print("-- per-column decisions (retrieval + propose + gates) --")
        decisions: list[Decision] = []
        for col in profile.columns:
            if col.prefilter is not None and col.prefilter.action == "exclude":
                d = Decision(
                    column=col.name,
                    position=col.position,
                    disposition="exclude",
                    rationale=f"prefilter[{col.prefilter.reason}]: {col.prefilter.evidence}",
                    confidence=1.0,
                    decided_by="rule",
                )
                print(f"  {col.name}: exclude (prefilter: {col.prefilter.reason})")
            else:
                d = decide_column(col, csv_name, subject, index, ontology, decisions, llm)
                target_desc = f"-> {d.target}" if d.target else (d.new_name or "")
                print(f"  {col.name}: {d.disposition} {target_desc} confidence={d.confidence:.2f} gates={d.gates_fired}")
            decisions.append(d)

        pre_budget_escalated = sum(1 for d in decisions if d.escalated)
        decisions = apply_escalation_budget(decisions, args.escalation_budget)
        final_escalated = [d for d in decisions if d.escalated]
        print(
            f"-- escalation budget: {pre_budget_escalated} flagged, {len(final_escalated)} kept "
            f"(budget={args.escalation_budget}), {pre_budget_escalated - len(final_escalated)} downgraded --"
        )

        escalation_entries: list[dict] = []
        if final_escalated:
            _banner(f"ESCALATION — {csv_name} ({len(final_escalated)} question(s))")
            profile_by_col = {c.name: c for c in profile.columns}
            questions = [
                build_question(d, profile_by_col[d.column], i, csv_name, qn)
                for qn, d in enumerate(final_escalated, start=1)
            ]
            answers_data = load_answers_file(args.answers)
            answers = ask(questions, answers_file=args.answers, interactive=True)

            columns_order = [d.column for d in decisions]
            decisions_by_col = {d.column: d for d in decisions}
            for q in questions:
                answer = answers[q.id]
                source = classify_source(q.id, answers_data, interactive=True)
                original = decisions_by_col[q.column]
                resolved = incorporate(
                    q,
                    answer,
                    original,
                    profile_by_col[q.column],
                    csv_name,
                    subject,
                    index,
                    ontology,
                    decisions,
                    llm,
                    source=source,
                )
                decisions_by_col[q.column] = resolved
                escalation_entries.append(
                    {
                        "id": q.id,
                        "question": q.question,
                        "answer": answer,
                        "source": source,
                        "resulting_decision": resolved.disposition + (f" -> {resolved.target}" if resolved.target else ""),
                    }
                )
            decisions = [decisions_by_col[col] for col in columns_order]

        print("-- assembling patch --")
        patch_ops = []
        if i == 1:
            # Whole-ontology issues found at startup are emitted in CSV1's
            # patch, per §2's hygiene-audit rule.
            patch_ops.extend(startup_flags)
        if subject.is_new:
            # Step A decided this CSV needs a brand-new subject type. That
            # type has to actually exist in the patch *before* any column's
            # add_attribute/add_relationship op below can reference it as
            # on_type -- patch.validate()'s types_added_in_patch tracking
            # (and apply()'s sequential mutation) both depend on this op
            # coming first.
            patch_ops.append(
                AddTypeOp(
                    name=subject.subject_type,
                    attributes=[],
                    rationale=subject.rationale,
                    confidence=subject.confidence,
                    description=subject.new_type_description or "",
                )
            )
        for d in decisions:
            op = decision_to_patch_op(d)
            if op is not None:
                patch_ops.append(op)
            patch_ops.extend(d.emitted_flags)

        errors = validate(patch_ops, ontology)
        if errors:
            _banner(f"VALIDATION ISSUES — {csv_name}")
            for err in errors:
                print(f"  ! {err}")
            print("  Applying nothing for this CSV (safety net) — patch/report are still written for inspection.")
            patched_ontology, applied_ops = patch_apply(patch_ops, ontology, mode="none")
        else:
            print(f"-- applying patch (mode={args.approve}) --")
            patched_ontology, applied_ops = patch_apply(patch_ops, ontology, mode=args.approve)
            applied_count = sum(1 for a in applied_ops if a.applied)
            print(f"  {applied_count}/{len(applied_ops)} ops applied")

        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        projection = project_rows(rows, decisions, patched_ontology, subject.subject_type, n=3)

        llm_usage = llm.usage if llm is not None else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_calls": 0}
        ontology_issue_ops = [op for op in patch_ops if isinstance(op, FlagOntologyIssueOp)]
        report_dict = build_report(
            csv_name=csv_name,
            row_count=profile.row_count,
            subject=subject,
            decisions=decisions,
            escalations=escalation_entries,
            ontology_issues=ontology_issue_ops,
            sample_rows=projection,
            llm_usage=llm_usage,
        )

        stem = _report_stem(csv_path, i)
        patch_path, report_json_path, report_md_path = write_report(args.out, stem, patch_ops, report_dict)
        print("-- wrote --")
        print(f"  {patch_path}")
        print(f"  {report_json_path}")
        print(f"  {report_md_path}")

        ontology = patched_ontology
        index.rebuild(build_cards(ontology, embedder=embedder))

        csv_summaries.append({"csv": csv_name, "stats": report_dict["stats"]})

    _banner("FINALIZING")
    final_ontology_path = os.path.join(args.out, "final_ontology.json")
    with open(final_ontology_path, "w", encoding="utf-8") as f:
        json.dump(ontology.to_json(), f, indent=2)
        f.write("\n")
    print(f"  wrote {final_ontology_path}")

    total_usage = llm.usage if llm is not None else {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_calls": 0}
    run_summary = {
        "csvs": csv_summaries,
        "totals": {
            key: sum(s["stats"][key] for s in csv_summaries)
            for key in ("columns", "reused", "new", "excluded", "escalated")
        },
        "llm_usage": total_usage,
        "wall_time_seconds": round(time.perf_counter() - start, 3),
    }
    run_summary_path = os.path.join(args.out, "run_summary.json")
    with open(run_summary_path, "w", encoding="utf-8") as f:
        json.dump(run_summary, f, indent=2)
        f.write("\n")
    print(f"  wrote {run_summary_path}")

    _banner("DONE")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    transcript_path = os.path.join(args.out, "transcript.md")

    with open(transcript_path, "w", encoding="utf-8") as transcript_file:
        original_stdout = sys.stdout
        sys.stdout = _Tee(original_stdout, transcript_file)
        try:
            _run(args)
        finally:
            sys.stdout = original_stdout

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
