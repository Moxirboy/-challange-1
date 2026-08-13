# Mapping report — 3_crm_export.csv

Row count: 6

## Subject type: `Person` (reused)
- confidence: 0.95
- rationale: The rows represent individual contacts within a CRM system, identified by 'contact_name' and 'email', which aligns best with the 'Person' entity type.

## Columns

| column | disposition | target | confidence | decided_by | gates_fired | escalated |
|---|---|---|---|---|---|---|
| _id | exclude | - | 1.0 | rule | - | False |
| contact_name | reuse | Person.full_name | 1.0 | llm | - | False |
| company | reuse | Organization.name | 0.95 | rule | near_duplicate | False |
| email | reuse | Person.email | 1.0 | llm | - | False |
| status | new_attribute | - | 0.95 | rule | vacuous_source | False |
| date | new_attribute | - | 0.68 | llm | vacuous_source, budget | yes (downgraded) |
| notes | new_attribute | - | 0.95 | llm | - | False |
| updated_at | exclude | - | 1.0 | rule | - | False |
| Unnamed: 8 | exclude | - | 1.0 | rule | - | False |

<details><summary>contact_name — retrieval candidates</summary>

- `Person.full_name` score=0.7391 (bm25=0.9173, embedding=0.6703, datatype_prior=1.0, shape_prior=0.0)
- `Organization.name` score=0.7116 (bm25=0.8808, embedding=0.6333, datatype_prior=1.0, shape_prior=0.0)
- `Person` score=0.4852 (bm25=0.5853, embedding=0.6445, datatype_prior=0.15, shape_prior=0.0)
- `Place.name` score=0.4549 (bm25=1.0, embedding=0.6456, datatype_prior=1.0, shape_prior=0.0)
- `Product.name` score=0.4519 (bm25=1.0, embedding=0.6327, datatype_prior=1.0, shape_prior=0.0)
- `Event.name` score=0.422 (bm25=0.8566, embedding=0.6339, datatype_prior=1.0, shape_prior=0.0)
- `Person.title` score=0.4094 (bm25=0.0, embedding=0.6485, datatype_prior=1.0, shape_prior=0.0)
- `Organization` score=0.406 (bm25=0.4115, embedding=0.5986, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)

</details>

<details><summary>company — retrieval candidates</summary>

- `Organization.name` score=0.8025 (bm25=1.0, embedding=0.7562, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.website` score=0.7089 (bm25=0.7994, embedding=0.6977, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.size` score=0.7024 (bm25=0.8181, embedding=0.6651, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.6922 (bm25=0.7612, embedding=0.6895, datatype_prior=1.0, shape_prior=0.0)
- `Person.works_at` score=0.5996 (bm25=0.941, embedding=0.6195, datatype_prior=0.15, shape_prior=0.0)
- `Organization.headquartered_in` score=0.5695 (bm25=0.8885, embedding=0.59, datatype_prior=0.15, shape_prior=0.0)
- `Organization.founded_year` score=0.5599 (bm25=0.7994, embedding=0.644, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.employee_count` score=0.5497 (bm25=0.7994, embedding=0.6185, datatype_prior=0.15, shape_prior=0.0)

</details>

<details><summary>email — retrieval candidates</summary>

- `Person.email` score=0.8925 (bm25=1.0, embedding=0.7312, datatype_prior=1.0, shape_prior=1.0)
- `Person` score=0.4909 (bm25=0.5853, embedding=0.6589, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.4135 (bm25=0.0, embedding=0.6587, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Person.title` score=0.4095 (bm25=0.0, embedding=0.6487, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.4048 (bm25=0.0, embedding=0.637, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Person.full_name` score=0.4046 (bm25=0.0, embedding=0.6365, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.4026 (bm25=0.0, embedding=0.6314, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.3913 (bm25=0.0, embedding=0.6031, datatype_prior=1.0, shape_prior=0.0)

</details>

<details><summary>status — retrieval candidates</summary>

- `Organization.name` score=0.4024 (bm25=0.0, embedding=0.6309, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.size` score=0.3994 (bm25=0.0, embedding=0.6236, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.3964 (bm25=0.0, embedding=0.616, datatype_prior=1.0, shape_prior=0.0)
- `Person.title` score=0.3957 (bm25=0.0, embedding=0.6142, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.3907 (bm25=0.0, embedding=0.6017, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Person.email` score=0.3821 (bm25=0.0, embedding=0.5803, datatype_prior=1.0, shape_prior=0.0)
- `Person.full_name` score=0.378 (bm25=0.0, embedding=0.57, datatype_prior=1.0, shape_prior=0.0)
- `Organization.founded_year` score=0.2551 (bm25=0.0, embedding=0.5815, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)

</details>

<details><summary>date — retrieval candidates</summary>

- `Product.launched_on` score=0.4516 (bm25=1.0, embedding=0.6316, datatype_prior=1.0, shape_prior=0.0)
- `Event.starts_on` score=0.4305 (bm25=0.8952, embedding=0.6354, datatype_prior=1.0, shape_prior=0.0)
- `Event.ends_on` score=0.4288 (bm25=0.8952, embedding=0.6282, datatype_prior=1.0, shape_prior=0.0)
- `JobPosting.posted_on` score=0.4164 (bm25=0.831, embedding=0.6326, datatype_prior=1.0, shape_prior=0.0)
- `Organization.name` score=0.2546 (bm25=0.0, embedding=0.5803, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization` score=0.2526 (bm25=0.0, embedding=0.5753, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Person.full_name` score=0.2521 (bm25=0.0, embedding=0.574, datatype_prior=0.15, shape_prior=0.0)
- `Person.title` score=0.2516 (bm25=0.0, embedding=0.5728, datatype_prior=0.15, shape_prior=0.0)

</details>

<details><summary>notes — retrieval candidates</summary>

- `Organization.industry` score=0.4129 (bm25=0.0, embedding=0.6571, datatype_prior=1.0, shape_prior=0.0)
- `Organization.name` score=0.4059 (bm25=0.0, embedding=0.6397, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Person.title` score=0.4017 (bm25=0.0, embedding=0.6294, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.4011 (bm25=0.0, embedding=0.6279, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.3906 (bm25=0.0, embedding=0.6014, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Person.email` score=0.381 (bm25=0.0, embedding=0.5775, datatype_prior=1.0, shape_prior=0.0)
- `Person.full_name` score=0.3798 (bm25=0.0, embedding=0.5744, datatype_prior=1.0, shape_prior=0.0)
- `Organization` score=0.2622 (bm25=0.0, embedding=0.5991, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)

</details>

## Escalations

### csv3.q1
- question: Column 'company' (sample values: ['Acme Industrial Group', 'Sakura Foods K.K.', 'Bluepeak Software', 'Nordwind Logistics', 'Verde Textiles']) -- the harness proposed 'new_relationship' -> Person.works_at. Is that right, or should it map to one of the candidates below instead?
- answer: reuse:Organization.name (source: default)
- resulting decision: reuse -> Organization.name

### csv3.q2
- question: Column 'status' (sample values: ['active', 'churned', 'prospect']) -- the harness proposed 'new_attribute' (status). Is that right, or should it map to one of the candidates below instead?
- answer: keep_original (source: default)
- resulting decision: new_attribute

## Sample-row projection

### Row 1
- `Person:1` (Person): attrs={'full_name': 'Dana Whitfield', 'email': 'dana.w@acmeindustrial.com', 'status': 'active', 'last_contacted_on': '2026-03-14', 'notes': 'renewal call went well'}, rels={'works_at': 'Organization:Acme Industrial Group'}
- `Organization:Acme Industrial Group` (Organization): attrs={'name': 'Acme Industrial Group'}, rels={}
- skipped: [{'column': '_id', 'reason': "excluded: prefilter[surrogate_key]: name '_id' is id-like and every non-null value is unique (6/6)"}, {'column': 'updated_at', 'reason': "excluded: prefilter[sync_metadata]: name 'updated_at' is a known sync-metadata field and is constant across rows"}, {'column': 'Unnamed: 8', 'reason': "excluded: prefilter[empty_column]: column 'Unnamed: 8' has no non-null values"}]

### Row 2
- `Person:2` (Person): attrs={'full_name': 'Kenji Mori', 'email': 'k.mori@sakurafoods.jp', 'status': 'churned', 'last_contacted_on': '2025-11-02', 'notes': 'moved to competitor'}, rels={'works_at': 'Organization:Sakura Foods K.K.'}
- `Organization:Sakura Foods K.K.` (Organization): attrs={'name': 'Sakura Foods K.K.'}, rels={}
- skipped: [{'column': '_id', 'reason': "excluded: prefilter[surrogate_key]: name '_id' is id-like and every non-null value is unique (6/6)"}, {'column': 'updated_at', 'reason': "excluded: prefilter[sync_metadata]: name 'updated_at' is a known sync-metadata field and is constant across rows"}, {'column': 'Unnamed: 8', 'reason': "excluded: prefilter[empty_column]: column 'Unnamed: 8' has no non-null values"}]

### Row 3
- `Person:3` (Person): attrs={'full_name': 'Priya Natarajan', 'email': 'priya@bluepeak.io', 'status': 'prospect', 'last_contacted_on': '2026-06-21', 'notes': 'met at ODSC booth'}, rels={'works_at': 'Organization:Bluepeak Software'}
- `Organization:Bluepeak Software` (Organization): attrs={'name': 'Bluepeak Software'}, rels={}
- skipped: [{'column': '_id', 'reason': "excluded: prefilter[surrogate_key]: name '_id' is id-like and every non-null value is unique (6/6)"}, {'column': 'updated_at', 'reason': "excluded: prefilter[sync_metadata]: name 'updated_at' is a known sync-metadata field and is constant across rows"}, {'column': 'Unnamed: 8', 'reason': "excluded: prefilter[empty_column]: column 'Unnamed: 8' has no non-null values"}]

## Stats

- columns: 9
- reused: 3
- new: 3
- excluded: 3
- escalated: 2
- llm_calls: 24
- cached_calls: 20
- prompt_tokens: 5742
- completion_tokens: 623
