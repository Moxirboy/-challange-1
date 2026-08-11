# Mapping report — 1_vendors.csv

Row count: 8

## Subject type: `Organization` (reused)
- confidence: 1.0
- rationale: The data describes business entities with attributes like sector, employee count, and headquarters location, which aligns perfectly with the definition of an Organization.

## Columns

| column | disposition | target | confidence | decided_by | gates_fired | escalated |
|---|---|---|---|---|---|---|
| vendor | reuse | Organization.name | 1.0 | llm | - | False |
| homepage_url | reuse | Organization.website | 1.0 | llm | - | False |
| established | reuse | Organization.founded_year | 1.0 | llm | - | False |
| sector | reuse | Organization.industry | 1.0 | llm | - | False |
| hq_city | reuse | Place.city | 0.9 | rule | near_duplicate | False |
| hq_country | reuse | Organization.headquartered_in | 0.95 | llm | - | False |
| employee_count | new_attribute | - | 1.0 | human | datatype_conflict | False |

<details><summary>vendor — retrieval candidates</summary>

- `Organization.name` score=0.7739 (bm25=1.0, embedding=0.6846, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.size` score=0.6907 (bm25=0.8195, embedding=0.6348, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.6859 (bm25=0.7628, embedding=0.6725, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.6837 (bm25=0.8008, embedding=0.6336, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.headquartered_in` score=0.5594 (bm25=0.8894, embedding=0.5641, datatype_prior=0.15, shape_prior=0.0)
- `Organization.founded_year` score=0.5359 (bm25=0.8008, embedding=0.5829, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization` score=0.5004 (bm25=0.6789, embedding=0.6006, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.city` score=0.3972 (bm25=0.0, embedding=0.6181, datatype_prior=1.0, shape_prior=0.0)

</details>

<details><summary>homepage_url — retrieval candidates</summary>

- `Organization.website` score=0.8927 (bm25=0.9994, embedding=0.7324, datatype_prior=1.0, shape_prior=1.0, aliased_from_twin=1.0)
- `Organization` score=0.5409 (bm25=0.741, embedding=0.6475, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.name` score=0.4129 (bm25=0.0, embedding=0.6571, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.industry` score=0.4086 (bm25=0.0, embedding=0.6465, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.4033 (bm25=0.0, embedding=0.6331, datatype_prior=1.0, shape_prior=0.0)
- `Place.country` score=0.3983 (bm25=0.0, embedding=0.6207, datatype_prior=1.0, shape_prior=0.0)
- `Place.city` score=0.3951 (bm25=0.0, embedding=0.6127, datatype_prior=1.0, shape_prior=0.0)
- `Place.name` score=0.3847 (bm25=0.0, embedding=0.5867, datatype_prior=1.0, shape_prior=0.0)

</details>

<details><summary>established — retrieval candidates</summary>

- `Organization.founded_year` score=0.773 (bm25=1.0, embedding=0.6825, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization` score=0.5213 (bm25=0.7415, embedding=0.5981, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.longitude` score=0.3151 (bm25=0.0, embedding=0.5626, datatype_prior=0.6, shape_prior=0.0)
- `Place.latitude` score=0.3117 (bm25=0.0, embedding=0.5543, datatype_prior=0.6, shape_prior=0.0)
- `Place` score=0.2501 (bm25=0.0, embedding=0.569, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.2368 (bm25=0.0, embedding=0.592, datatype_prior=0.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.country` score=0.2356 (bm25=0.0, embedding=0.589, datatype_prior=0.0, shape_prior=0.0)
- `Organization.headquartered_in` score=0.2337 (bm25=0.0, embedding=0.5279, datatype_prior=0.15, shape_prior=0.0)

</details>

<details><summary>sector — retrieval candidates</summary>

- `Organization.industry` score=0.7807 (bm25=1.0, embedding=0.7019, datatype_prior=1.0, shape_prior=0.0)
- `Organization` score=0.4306 (bm25=0.4644, embedding=0.6139, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.4181 (bm25=0.0, embedding=0.6703, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.size` score=0.4095 (bm25=0.0, embedding=0.6488, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.402 (bm25=0.0, embedding=0.6301, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.country` score=0.3991 (bm25=0.0, embedding=0.6228, datatype_prior=1.0, shape_prior=0.0)
- `Place.city` score=0.3953 (bm25=0.0, embedding=0.6132, datatype_prior=1.0, shape_prior=0.0)
- `Place.name` score=0.3827 (bm25=0.0, embedding=0.5818, datatype_prior=1.0, shape_prior=0.0)

</details>

<details><summary>hq_city — retrieval candidates</summary>

- `Place.city` score=0.7842 (bm25=1.0, embedding=0.7105, datatype_prior=1.0, shape_prior=0.0)
- `Organization.headquartered_in` score=0.5893 (bm25=0.8544, embedding=0.6693, datatype_prior=0.15, shape_prior=0.0)
- `Place` score=0.5163 (bm25=0.6855, embedding=0.6347, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.4181 (bm25=0.0, embedding=0.6702, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.country` score=0.4175 (bm25=0.0, embedding=0.6686, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.4073 (bm25=0.0, embedding=0.6434, datatype_prior=1.0, shape_prior=0.0)
- `Place.name` score=0.4056 (bm25=0.0, embedding=0.6389, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.4042 (bm25=0.0, embedding=0.6356, datatype_prior=1.0, shape_prior=0.0)

</details>

<details><summary>hq_country — retrieval candidates</summary>

- `Place.country` score=0.789 (bm25=1.0, embedding=0.7226, datatype_prior=1.0, shape_prior=0.0)
- `Organization.headquartered_in` score=0.5909 (bm25=0.8544, embedding=0.6735, datatype_prior=0.15, shape_prior=0.0)
- `Place` score=0.5205 (bm25=0.6855, embedding=0.6453, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.4186 (bm25=0.0, embedding=0.6716, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.city` score=0.4128 (bm25=0.0, embedding=0.6571, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.4073 (bm25=0.0, embedding=0.6432, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.4061 (bm25=0.0, embedding=0.6402, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.size` score=0.4048 (bm25=0.0, embedding=0.6371, datatype_prior=1.0, shape_prior=0.0)

</details>

<details><summary>employee_count — retrieval candidates</summary>

- `Organization.size` score=0.6139 (bm25=1.0, embedding=0.6596, datatype_prior=0.0, shape_prior=0.0)
- `Organization` score=0.4389 (bm25=0.5134, embedding=0.5919, datatype_prior=0.15, shape_prior=0.0)
- `Organization.founded_year` score=0.4143 (bm25=0.0, embedding=0.6608, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Place.latitude` score=0.3172 (bm25=0.0, embedding=0.5681, datatype_prior=0.6, shape_prior=0.0)
- `Place.longitude` score=0.313 (bm25=0.0, embedding=0.5576, datatype_prior=0.6, shape_prior=0.0)
- `Organization.name` score=0.2539 (bm25=0.0, embedding=0.6348, datatype_prior=0.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.headquartered_in` score=0.2475 (bm25=0.0, embedding=0.5626, datatype_prior=0.15, shape_prior=0.0)
- `Place` score=0.2471 (bm25=0.0, embedding=0.5616, datatype_prior=0.15, shape_prior=0.0)

</details>

## Escalations

### csv1.q1
- question: Column 'hq_city' (sample values: ['Cleveland', 'Hamburg', 'Austin', 'Osaka', 'Denver']) -- the harness proposed 'new_relationship' (headquartered_in). Is that right, or should it map to one of the candidates below instead?
- answer: reuse:Place.city (source: default)
- resulting decision: reuse -> Place.city

### csv1.q2
- question: Column 'employee_count' (sample values: ['540', '1230', '88', '2100', '410']) -- the harness proposed 'reuse' -> Organization.size. Is that right, or should it map to one of the candidates below instead?
- answer: Do NOT reuse Organization.size. `size` is a different concept: a coarse size band (e.g. 'SMB', 'Enterprise'), which is why it is typed string. `employee_count` is an exact headcount, so add a new attribute `employee_count` of datatype integer on Organization, aligned with https://schema.org/numberOfEmployees. Leave Organization.size unchanged, but flag it as an ontology issue because its description 'Size of the organization' is too vague to distinguish it from headcount. (source: file)
- resulting decision: new_attribute

## Ontology issues

- [warning] Organization / Company: 'Organization' and 'Company' look like near-duplicate types (name_similarity=1.00, attribute_overlap=0.60, score=0.80 >= 0.5); canonical type is 'Organization' (richer: more attributes/relationships and/or descriptions) -- retrieval resolves 'Company' concepts onto it; consider deleting 'Company' from the ontology.
- [warning] Person.data: attribute 'Person.data' is vacuous (generic name or placeholder description); demote, don't reuse
- [warning] Organization.size: attribute 'Organization.size' name implies a quantity but its datatype is 'string'; likely should be integer/number

## Sample-row projection

### Row 1
- `Organization:1` (Organization): attrs={'name': 'Acme Industrial Group', 'website': 'https://acmeindustrial.com', 'founded_year': '1987', 'industry': 'Manufacturing', 'employee_count': '540'}, rels={'headquartered_in': 'Place:Cleveland'}
- `Place:Cleveland` (Place): attrs={'city': 'Cleveland'}, rels={}
- `Place:USA` (Place): attrs={'name': 'USA'}, rels={}

### Row 2
- `Organization:2` (Organization): attrs={'name': 'Nordwind Logistics', 'website': 'https://nordwind-logistics.de', 'founded_year': '2004', 'industry': 'Logistics', 'employee_count': '1230'}, rels={'headquartered_in': 'Place:Hamburg'}
- `Place:Hamburg` (Place): attrs={'city': 'Hamburg'}, rels={}
- `Place:Germany` (Place): attrs={'name': 'Germany'}, rels={}

### Row 3
- `Organization:3` (Organization): attrs={'name': 'Bluepeak Software', 'website': 'https://bluepeak.io', 'founded_year': '2016', 'industry': 'Software', 'employee_count': '88'}, rels={'headquartered_in': 'Place:Austin'}
- `Place:Austin` (Place): attrs={'city': 'Austin'}, rels={}
- `Place:USA` (Place): attrs={'name': 'USA'}, rels={}

## Stats

- columns: 7
- reused: 6
- new: 1
- excluded: 0
- escalated: 2
- llm_calls: 9
- cached_calls: 9
- prompt_tokens: 0
- completion_tokens: 0
