# Mapping report — 2_product_catalog.csv

Row count: 7

## Subject type: `Product` (reused)
- confidence: 1.0
- rationale: The CSV contains information about items being sold, including product names, SKUs, manufacturers, and pricing, which aligns perfectly with the definition of a Product.

## Columns

| column | disposition | target | confidence | decided_by | gates_fired | escalated |
|---|---|---|---|---|---|---|
| product_name | reuse | Product.name | 1.0 | llm | - | False |
| sku | reuse | Product.sku | 1.0 | llm | - | False |
| manufacturer | reuse | Product.made_by | 0.9 | rule | near_duplicate | False |
| msrp | reuse | Product.price_usd | 0.95 | llm | - | False |
| warranty_months | new_attribute | - | 0.95 | llm | - | False |
| distributor | new_relationship | distributor | 0.95 | llm | - | False |
| country_of_origin | new_relationship | - | 0.95 | llm | - | False |

<details><summary>product_name — retrieval candidates</summary>

- `Product.name` score=0.7899 (bm25=1.0, embedding=0.7248, datatype_prior=1.0, shape_prior=0.0)
- `Product.sku` score=0.5504 (bm25=0.397, embedding=0.6536, datatype_prior=1.0, shape_prior=0.0)
- `Organization.name` score=0.5113 (bm25=0.3488, embedding=0.5981, datatype_prior=1.0, shape_prior=0.0)
- `Product` score=0.4886 (bm25=0.5582, embedding=0.6768, datatype_prior=0.15, shape_prior=0.0)
- `Product.launched_on` score=0.4144 (bm25=0.4216, embedding=0.6107, datatype_prior=0.15, shape_prior=0.0)
- `Organization.industry` score=0.4005 (bm25=0.0, embedding=0.6262, datatype_prior=1.0, shape_prior=0.0)
- `Product.price_usd` score=0.399 (bm25=0.3378, embedding=0.6455, datatype_prior=0.15, shape_prior=0.0)
- `Product.made_by` score=0.3897 (bm25=0.3378, embedding=0.6223, datatype_prior=0.15, shape_prior=0.0)

</details>

<details><summary>sku — retrieval candidates</summary>

- `Product.sku` score=0.7957 (bm25=1.0, embedding=0.7391, datatype_prior=1.0, shape_prior=0.0)
- `Product.name` score=0.4078 (bm25=0.0, embedding=0.6445, datatype_prior=1.0, shape_prior=0.0)
- `Organization.name` score=0.4051 (bm25=0.0, embedding=0.6378, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.industry` score=0.3959 (bm25=0.0, embedding=0.6147, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.3933 (bm25=0.0, embedding=0.6084, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.3915 (bm25=0.0, embedding=0.6037, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Product` score=0.2806 (bm25=0.0, embedding=0.6453, datatype_prior=0.15, shape_prior=0.0)
- `Product.price_usd` score=0.2644 (bm25=0.0, embedding=0.6047, datatype_prior=0.15, shape_prior=0.0)

</details>

<details><summary>manufacturer — retrieval candidates</summary>

- `Product.made_by` score=0.6472 (bm25=1.0, embedding=0.6868, datatype_prior=0.15, shape_prior=0.0)
- `Organization.name` score=0.4277 (bm25=0.0, embedding=0.6943, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.industry` score=0.4266 (bm25=0.0, embedding=0.6914, datatype_prior=1.0, shape_prior=0.0)
- `Product.name` score=0.415 (bm25=0.0, embedding=0.6624, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.4081 (bm25=0.0, embedding=0.6454, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.4077 (bm25=0.0, embedding=0.6442, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Product.sku` score=0.4064 (bm25=0.0, embedding=0.6411, datatype_prior=1.0, shape_prior=0.0)
- `Organization` score=0.2713 (bm25=0.0, embedding=0.622, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)

</details>

<details><summary>msrp — retrieval candidates</summary>

- `Product.price_usd` score=0.768 (bm25=0.9842, embedding=0.6838, datatype_prior=1.0, shape_prior=0.0)
- `Product.made_by` score=0.595 (bm25=1.0, embedding=0.5563, datatype_prior=0.15, shape_prior=0.0)
- `Product` score=0.4281 (bm25=0.4718, embedding=0.6012, datatype_prior=0.15, shape_prior=0.0)
- `Organization.founded_year` score=0.3216 (bm25=0.0, embedding=0.5789, datatype_prior=0.6, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.employee_count` score=0.3157 (bm25=0.0, embedding=0.5643, datatype_prior=0.6, shape_prior=0.0)
- `Organization` score=0.2533 (bm25=0.0, embedding=0.577, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.name` score=0.2358 (bm25=0.0, embedding=0.5895, datatype_prior=0.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Product.name` score=0.2357 (bm25=0.0, embedding=0.5892, datatype_prior=0.0, shape_prior=0.0)

</details>

<details><summary>warranty_months — retrieval candidates</summary>

- `Organization.founded_year` score=0.4103 (bm25=0.0, embedding=0.6507, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.employee_count` score=0.3985 (bm25=0.0, embedding=0.6212, datatype_prior=1.0, shape_prior=0.0)
- `Product.price_usd` score=0.3377 (bm25=0.0, embedding=0.6194, datatype_prior=0.6, shape_prior=0.0)
- `Product.made_by` score=0.2686 (bm25=0.0, embedding=0.6154, datatype_prior=0.15, shape_prior=0.0)
- `Product` score=0.2672 (bm25=0.0, embedding=0.6118, datatype_prior=0.15, shape_prior=0.0)
- `Organization` score=0.2569 (bm25=0.0, embedding=0.586, datatype_prior=0.15, shape_prior=0.0, aliased_from_twin=1.0)
- `Product.launched_on` score=0.248 (bm25=0.0, embedding=0.6199, datatype_prior=0.0, shape_prior=0.0)
- `Product.sku` score=0.2476 (bm25=0.0, embedding=0.619, datatype_prior=0.0, shape_prior=0.0)

</details>

<details><summary>distributor — retrieval candidates</summary>

- `Organization.name` score=0.4079 (bm25=0.0, embedding=0.6448, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Organization.industry` score=0.4048 (bm25=0.0, embedding=0.637, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.3975 (bm25=0.0, embedding=0.6188, datatype_prior=1.0, shape_prior=0.0)
- `Product.sku` score=0.397 (bm25=0.0, embedding=0.6176, datatype_prior=1.0, shape_prior=0.0)
- `Product.name` score=0.3964 (bm25=0.0, embedding=0.6159, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.395 (bm25=0.0, embedding=0.6126, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Product.made_by` score=0.269 (bm25=0.0, embedding=0.6162, datatype_prior=0.15, shape_prior=0.0)
- `Product` score=0.2651 (bm25=0.0, embedding=0.6065, datatype_prior=0.15, shape_prior=0.0)

</details>

<details><summary>country_of_origin — retrieval candidates</summary>

- `Place.country` score=0.4704 (bm25=1.0, embedding=0.7101, datatype_prior=1.0, shape_prior=0.0)
- `Organization.name` score=0.4094 (bm25=0.0, embedding=0.6486, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Product.name` score=0.4038 (bm25=0.0, embedding=0.6344, datatype_prior=1.0, shape_prior=0.0)
- `Organization.industry` score=0.4011 (bm25=0.0, embedding=0.6277, datatype_prior=1.0, shape_prior=0.0)
- `Organization.size` score=0.3968 (bm25=0.0, embedding=0.6171, datatype_prior=1.0, shape_prior=0.0)
- `Product.sku` score=0.3958 (bm25=0.0, embedding=0.6146, datatype_prior=1.0, shape_prior=0.0)
- `Organization.website` score=0.3945 (bm25=0.0, embedding=0.6113, datatype_prior=1.0, shape_prior=0.0, aliased_from_twin=1.0)
- `Place` score=0.3105 (bm25=0.6863, embedding=0.6372, datatype_prior=0.15, shape_prior=0.0)

</details>

## Escalations

### csv2.q1
- question: Column 'manufacturer' (sample values: ['Acme Industrial Group', 'Helios Energy', 'Verde Textiles', 'Sakura Foods K.K.', 'Bluepeak Software']) -- the harness proposed 'new_relationship' (manufacturer). Is that right, or should it map to one of the candidates below instead?
- answer: reuse:Product.made_by (source: default)
- resulting decision: reuse -> Product.made_by

## Sample-row projection

### Row 1
- `Product:1` (Product): attrs={'name': 'Volt 9 Cordless Drill', 'sku': 'VD-0091', 'price_usd': '129.99', 'warranty_period_months': '24'}, rels={'made_by': 'Organization:Acme Industrial Group', 'distributor': 'Organization:Toolhaus GmbH', 'country_of_origin': 'Place:USA'}
- `Organization:Acme Industrial Group` (Organization): attrs={'name': 'Acme Industrial Group'}, rels={}
- `Organization:Toolhaus GmbH` (Organization): attrs={'name': 'Toolhaus GmbH'}, rels={}
- `Place:USA` (Place): attrs={'name': 'USA'}, rels={}

### Row 2
- `Product:2` (Product): attrs={'name': 'Volt 9 Impact Driver', 'sku': 'VD-0112', 'price_usd': '149.99', 'warranty_period_months': '24'}, rels={'made_by': 'Organization:Acme Industrial Group', 'distributor': 'Organization:Toolhaus GmbH', 'country_of_origin': 'Place:USA'}
- `Organization:Acme Industrial Group` (Organization): attrs={'name': 'Acme Industrial Group'}, rels={}
- `Organization:Toolhaus GmbH` (Organization): attrs={'name': 'Toolhaus GmbH'}, rels={}
- `Place:USA` (Place): attrs={'name': 'USA'}, rels={}

### Row 3
- `Product:3` (Product): attrs={'name': 'Aurora 400W Solar Panel', 'sku': 'AU-4001', 'price_usd': '289.00', 'warranty_period_months': '120'}, rels={'made_by': 'Organization:Helios Energy', 'distributor': 'Organization:SunGrid Distribution', 'country_of_origin': 'Place:USA'}
- `Organization:Helios Energy` (Organization): attrs={'name': 'Helios Energy'}, rels={}
- `Organization:SunGrid Distribution` (Organization): attrs={'name': 'SunGrid Distribution'}, rels={}
- `Place:USA` (Place): attrs={'name': 'USA'}, rels={}

## Stats

- columns: 7
- reused: 4
- new: 3
- excluded: 0
- escalated: 1
- llm_calls: 17
- cached_calls: 17
- prompt_tokens: 0
- completion_tokens: 0
