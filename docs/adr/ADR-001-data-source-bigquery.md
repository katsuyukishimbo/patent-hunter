# ADR-001: Google Patents Public Datasets (BigQuery) as the patent data source

## Status
Accepted (2026-05-14)

## Context

Patent Hunter needs to fetch newly-expired US utility patents every
week. Three obvious data sources exist:

1. **PatentsView REST API** (`api.patentsview.org`) — the route the
   original Gipp pipeline used.
2. **USPTO Open Data Portal (ODP)** at `data.uspto.gov` — the
   officially-blessed successor.
3. **Google Patents Public Datasets on BigQuery**
   (`patents-public-data.patents.publications`) — Google's mirror of
   USPTO data, queryable via SQL.

During implementation, two of those routes turned out to be unusable:

| Route | Reason it does not work in 2026 |
|---|---|
| PatentsView REST | **Decommissioned 2025-05-01.** Every call returns HTTP 301 to `data.uspto.gov/support/transition-guide/patentsview`. The replacement "PatentSearch API" at `search.patentsview.org/api/v1/patent` also redirects after the 2026-03-20 ODP migration. |
| USPTO ODP API key | Issuance requires an **ID.me** account, which in turn requires a US-government-issued ID + a US Social Security Number + US-based identity verification. Not attainable from Japan without circumventing the verification. |

That left BigQuery as the only practical option.

## Decision

Use **Google Patents Public Datasets on BigQuery**
(`patents-public-data.patents.publications`) as the production data
source. Authenticate via gcloud Application Default Credentials
(ADC). The fetcher lives at `src/patent_hunter/fetchers/bigquery.py`
and conforms to `FetcherProtocol` so a future fetcher (Lens.org, a
new USPTO API, etc.) can be slotted in without touching downstream
code.

The legacy implementation against PatentsView is preserved as
`src/patent_hunter/fetchers/legacy_patentsview.py` for reference. It
is no longer wired into the runner.

## Consequences

Positive:

- Authentication works from any country with a Google account; no
  government-issued ID is needed.
- SQL is significantly more expressive than the legacy REST query
  language. Multi-CPC filtering with vintage and assignee size in a
  single query takes one trip instead of N.
- 1 TB/month is free; weekly runs touch <100 MB. Cost is effectively
  zero.

Negative:

- 24-hour data lag versus USPTO's first publication. Irrelevant for
  the project because we filter on grants from ~12 years ago.
- Adds the `google-cloud-bigquery` Python dependency (~50 MB of
  transitive packages).
- Requires a billing-enabled GCP project. The BigQuery API cannot be
  enabled without a billing account attached, even if the queries
  stay in the free tier.

## Alternatives considered

- **USPTO Bulk Data (XML dumps)** — fully public, no auth required,
  but each weekly file is 1–3 GB and requires lxml parsing taking
  10–15 min per run. We deemed it too heavy for the P1 CLI budget of
  "complete a cycle in under two hours including LLM scoring".
- **Lens.org API** — commercial, ~$500/month, gated behind a sales
  process. Overkill for a single-operator experiment.
- **Scraping Google Patents Web** — would violate Google's ToS and
  break the moment they change the DOM.

## References

- PatentsView legacy decommission notice:
  https://patentsview.org/data-in-action/support-legacy-api-end-february-2025-switch-patentsearch-api-now
- USPTO ODP transition guide:
  https://data.uspto.gov/support/transition-guide/patentsview
- BigQuery dataset card:
  https://console.cloud.google.com/marketplace/details/google_patents_public_datasets/google-patents-public-data
- Implementation: `src/patent_hunter/fetchers/bigquery.py`
- Legacy reference: `src/patent_hunter/fetchers/legacy_patentsview.py`
