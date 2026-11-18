# Langfuse public API shapes for a fake server (scores v3, observations v2)

Researched 2026-11-10. No Langfuse project API was called. Sources were fetched that day:

- **SPEC** = OpenAPI 3.0.1 document at https://cloud.langfuse.com/generated/api/openapi.yml (`info.version` is empty: `''`). Spec locations are given as JSON-pointer-ish paths, e.g. `paths./api/public/v3/scores.get.parameters[name=limit]` or `components.schemas.BaseScoreV3.properties.comment`.
- **DOC:public-api** = https://langfuse.com/docs/api-and-data-platform/features/public-api.md
- **DOC:migration** = https://langfuse.com/faq/all/deprecated-api-migration.md
- **DOC:export** = https://langfuse.com/faq/all/export-single-trace-or-session.md
- **DOC:limits** = https://langfuse.com/faq/all/api-limits.md
- **DOC:judge** = https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge.md
- **DOC:url** = https://langfuse.com/docs/observability/features/url.md
- **DOC:scores-model** = https://langfuse.com/docs/evaluation/scores/data-model.md (found via https://langfuse.com/llms.txt, then https://langfuse.com/llms-docs.txt)
- **DOC:versioning** = https://langfuse.com/self-hosting/upgrade/versioning.md (self-hosted feature matrix)
- **DOC:v3-to-v4** = https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4.md
- **DOC:health** = https://langfuse.com/self-hosting/configuration/health-readiness-endpoints.md

Provenance tags used below: **[spec]** = from the OpenAPI spec, **[doc: X]** = from a docs page, **[not stated]** = not stated anywhere I could find. A **CONFLICT** line flags a place where docs and spec disagree. What the spec's descriptions and the docs say is given in our own words; field names, paths, enum values, status codes and other identifiers are kept as they are.

---

## Headline surprises (read first)

1. **The v3 score object has no `stringValue`, and no flat `traceId`, `observationId` or `sessionId`.** [spec: `components.schemas.BaseScoreV3`, `ScoreV3`]
   - It has a single polymorphic `value`: a number for NUMERIC, a boolean for BOOLEAN, and a string for CATEGORICAL, TEXT and CORRECTION.
   - What the score is attached to comes back only as a nested `subject` object, and only with `fields=subject`.
   - The author field is `authorUserId`, returned only with `fields=annotation`.
   - The older shape (flat ids, `value` + `stringValue`) belongs to the deprecated `GET /api/public/scores` and `GET /api/public/v2/scores`. [doc: migration#scores]
2. **Observations v2 `input` and `output` are raw strings, not parsed JSON.** `parseIoAsJson=true` returns 400. [spec: `paths./api/public/v2/observations.get.parameters[name=parseIoAsJson]`; doc: public-api, migration]
3. **On both endpoints the last page is marked by a missing `meta.cursor`.** For observations, an empty `data` with a cursor present is legal and means the client should ask for the next page. [spec: `components.schemas.utilsCursorMetaResponse`, `ObservationsV2Meta`]
4. **No error body schema is declared for either endpoint.** Both list 400/401/403/404/405 with `schema: {}`, and neither declares a 429 at all. [spec]

---

## A. `GET /api/public/v3/scores`

Spec location: `paths./api/public/v3/scores.get`. Its `operationId` is `scores_getMany`, its tag is `Scores`, and `security` is `BasicAuth` (via the YAML anchor `*ref_0`, which resolves to `- BasicAuth: []`). [spec]

### A.1 Query parameters

All parameters are `in: query` and `required: false`. Every string parameter is typed `type: string, nullable: true`. [spec: `paths./api/public/v3/scores.get.parameters`]

| Name | Type (spec) | Allowed values / semantics | Default |
|---|---|---|---|
| `limit` | integer, nullable | At most 100, and 50 when not given; a limit above 100 gets a 400. [spec] | 50 [spec, description text] |
| `cursor` | string, nullable | A base64url cursor. Sending back the one from the previous response gives the next page; the last page carries none. [spec] | none |
| `fields` | string, nullable | Comma-separated groups added to the core fields. The groups are `details`, `subject` and `annotation`; any other name gets a 400. [spec] | No extra groups, so core only. [spec: the endpoint description says the core fields come back in every case; that omitting `fields` returns exactly core is implied, not stated outright] |
| `id` | string | Comma-separated score IDs. Any of the values in one filter may match, and every filter given must match. [spec] | none |
| `name` | string | Comma-separated score names [spec] | none |
| `source` | string | Comma-separated, e.g. `API`, `ANNOTATION`, `EVAL`. Case does not matter: `api` matches as `API` does. [spec]. The enum `components.schemas.ScoreSource` = `ANNOTATION`, `API`, `EVAL` [spec] | none |
| `dataType` | string | Comma-separated `NUMERIC`, `BOOLEAN`, `CATEGORICAL`, `TEXT`, `CORRECTION`, case-insensitive. Next to `value`, `valueMin` or `valueMax` it has to name exactly one type, or the request gets a 400; next to `valueMin` or `valueMax` that type has to be `NUMERIC`. [spec] | none |
| `environment` | string | Comma-separated environments [spec] | none |
| `configId` | string | Comma-separated score config IDs [spec] | none |
| `queueId` | string | Comma-separated annotation queue IDs [spec] | none |
| `authorUserId` | string | Comma-separated author user IDs [spec] | none |
| `value` | string | Comma-separated exact values. Allowed only with exactly one `dataType`, and that one `NUMERIC`, `BOOLEAN` or `CATEGORICAL`; another type, several types or no `dataType` at all gets a 400. Each BOOLEAN value is `true` or `false`, and each NUMERIC value a finite number. [spec] | none |
| `valueMin` | number (double), nullable | Inclusive lower bound; requires `dataType=NUMERIC` as a single value, else 400 [spec] | none |
| `valueMax` | number (double), nullable | Inclusive upper bound; same rule [spec] | none |
| `traceId` | string | Comma-separated trace IDs. Cannot be given with `sessionId` or `experimentId`. Can be given with `observationId`, and then the observation is looked for within that trace. [spec] | none |
| `sessionId` | string | Comma-separated. Cannot be given with `traceId`, `observationId` or `experimentId`. [spec] | none |
| `observationId` | string | Comma-separated. Needs `traceId` as well, and gets a 400 without it. Cannot be given with `sessionId` or `experimentId`. [spec] | none |
| `experimentId` | string | Comma-separated dataset run IDs. Mutually exclusive with `traceId`, `sessionId`, `observationId` [spec] | none |
| `fromTimestamp` | string, `format: date-time`, nullable | Lower bound on the score's `timestamp`, inclusive. [spec] | none |
| `toTimestamp` | string, `format: date-time`, nullable | Upper bound on the score's `timestamp`, exclusive. [spec] | none |

Further notes on the parameters:

- **No other parameters exist.** There is no `page`, `userId`, `traceTags`, `operator` or JSON `filter` on v3. [spec: the parameter list is exhaustive; doc: migration#scores, whose parameter mapping table marks `userId`, `traceTags` and `filter` as removed]
- **Mutual exclusivity in the docs is less complete than in the spec.**
  - DOC:public-api#scores-filtering makes `traceId`, `sessionId` and `experimentId` exclusive of one another, and makes `observationId` need `traceId`.
  - The spec additionally makes `sessionId` exclusive with `observationId`, and `experimentId` exclusive with `observationId`.
  - The two sources are consistent, but the spec is more complete.
- **Invalid combinations return 400.** A filter combination that is not allowed gets a 400; it is never quietly ignored. [doc: public-api#scores-filtering]
- **Change filters between pages at your own risk.** For the next page, the docs say to send the `meta.cursor` you got together with the same filters as before [doc: public-api#scores-filtering]. What happens if the filters change between pages is [not stated].
- **Does `traceId=T` alone return observation-level scores inside trace T?** This is [not stated]. A score's `subject` is exactly one of trace, observation, session or experiment [spec: `ScoreSubjectV3`; doc: public-api#scores-subject], and the `observationId` description says `traceId` narrows the search for the observation to one trace. Whether the `traceId` filter matches `subject.kind=="observation"` scores whose `subject.traceId==T` is not said anywhere. **A fake server has to choose, and the connector should be tested both ways.**
- **Sort order of v3 scores is [not stated].** It is not given in the spec or in DOC:public-api.

### A.2 Response 200 shape

`content.application/json.schema` = `$ref: '#/components/schemas/GetScoresV3Response'` [spec]

```
GetScoresV3Response (object)            [spec: components.schemas.GetScoresV3Response]
  data: array of ScoreV3   (required)
  meta: GetScoresV3Meta    (required)

GetScoresV3Meta (object)                [spec: components.schemas.GetScoresV3Meta]
  limit:  integer          (required)
  allOf utilsCursorMetaResponse:
    cursor: string, nullable, NOT required
            (described as an opaque cursor for the next page, left out when there is none)
                                         [spec: components.schemas.utilsCursorMetaResponse.properties.cursor]
```

- **The list key is `data`.** [spec]
- **The pagination object is `meta`, with keys `limit` (required) and `cursor` (optional).** [spec]
- **Next page:** send `cursor=<meta.cursor>` with the same filters. [spec: param description; doc: public-api]
- **Last page:** `meta.cursor` is absent. The spec's descriptions say the field is left out on the last page, but the spec also types it `nullable: true`. Whether the real server ever sends `"cursor": null` is [not stated]. A robust client should treat both absent and `null` as the end. For a fake, leaving the key out follows what the spec's descriptions say.
- **There is no `page`, `totalItems` or `totalPages`** (unlike the deprecated `utilsMetaResponse`). [spec]

#### ScoreV3 (discriminated by `dataType`)

[spec: `components.schemas.ScoreV3`]

`oneOf` five variants. Each variant is `allOf [ {dataType: enum [X]}, <X>ScoreV3 ]` with `required: [dataType]`, and each `<X>ScoreV3` = `allOf BaseScoreV3` + `value` (required):

| `dataType` | Variant schema | `value` type |
|---|---|---|
| `NUMERIC` | `NumericScoreV3` | number, format double |
| `BOOLEAN` | `BooleanScoreV3` | boolean |
| `CATEGORICAL` | `CategoricalScoreV3` | string (the category) |
| `TEXT` | `TextScoreV3` | string |
| `CORRECTION` | `CorrectionScoreV3` | string (`""` when not set) |

[spec: `components.schemas.{Numeric,Boolean,Categorical,Text,Correction}ScoreV3.properties.value`; the same table is in doc: public-api#scores-value]

#### BaseScoreV3 fields

[spec: `components.schemas.BaseScoreV3`]

| Field | Type | Nullable | Required | Present when |
|---|---|---|---|---|
| `id` | string | no | yes | always (core) |
| `projectId` | string | no | yes | always |
| `name` | string | no | yes | always |
| `source` | `ScoreSource` enum `ANNOTATION` / `API` / `EVAL` | no | yes | always |
| `timestamp` | string date-time | no | yes | always |
| `environment` | string | no | yes | always |
| `createdAt` | string date-time | no | yes | always |
| `updatedAt` | string date-time | no | yes | always |
| `dataType` | enum (see above) | no | yes (discriminator) | always |
| `value` | number / boolean / string by `dataType` | no | yes | always |
| `comment` | string | yes | no | `fields` includes `details` |
| `configId` | string | yes | no | `fields` includes `details` |
| `metadata` | object, `additionalProperties: true` | yes | no | `fields` includes `details` |
| `authorUserId` | string | yes | no | `fields` includes `annotation` |
| `queueId` | string | yes | no | `fields` includes `annotation` |
| `subject` | `ScoreSubjectV3` | yes | no | `fields` includes `subject` |

The core list in the endpoint description is: id, projectId, name, value, dataType, source, timestamp, environment, createdAt, updatedAt. [spec: `paths./api/public/v3/scores.get.description`; doc: public-api#scores-field-groups agrees]

**Fields that do not exist on a v3 score.** This maps the task's requested field list onto v3. [spec]

- **String value for categorical scores:** there is no `stringValue`. The category is `value` (a string) when `dataType == "CATEGORICAL"`.
- **traceId / observationId / sessionId:** there is no top-level field. Use `subject` instead:
  - `{kind:"trace", id}`
  - `{kind:"observation", id, traceId}`
  - `{kind:"session", id}`
  - `{kind:"experiment", id}`
- **Author:** `authorUserId` (annotation group).
- **Config id:** `configId` (details group).
- **Timestamps:** `timestamp`, `createdAt`, `updatedAt` (core).

**Absent vs null for groups you did not request: [not stated] for scores.**
- Only for observations v2 do the docs say that a group not asked for is left out rather than sent as null (doc: public-api#field-groups).
- For scores, the spec's descriptions say only that each such field comes back when its group is named in `fields`, and the spec marks the properties nullable.
- A fake should probably omit them, but that is an inference.

#### ScoreSubjectV3

[spec: `components.schemas.ScoreSubjectV3` and `ScoreSubject{Trace,Observation,Session,Experiment}V3`]

`oneOf`, discriminated by `kind` (required):

| `kind` | Other properties |
|---|---|
| `"trace"` | `id` (string, required; the trace ID) |
| `"observation"` | `id` (string, required; the observation ID), `traceId` (string, **nullable**, not required: the parent trace's ID, when known) |
| `"session"` | `id` (string, required) |
| `"experiment"` | `id` (string, required; the dataset run ID) |

A made-up example of the shape DOC:public-api#scores-subject shows: `{ "kind": "observation", "id": "obs-7c2", "traceId": "trace-41a" }`.

### A.3 Example response (as the spec implies)

Request: `GET /api/public/v3/scores?traceId=7f1c...&fields=details,subject,annotation&limit=2`. This is not the last page, so `cursor` is present. The id, timestamp and cursor values are illustrative. The exact timestamp serialization (milliseconds, `Z`) is [not stated]; the spec says only `format: date-time`.

```json
{
  "data": [
    {
      "id": "b4e9b6c2-3c1e-4c7e-9f0a-1d2e3f4a5b6c",
      "projectId": "cm0proj000001",
      "name": "answer_correctness",
      "dataType": "CATEGORICAL",
      "value": "partially_correct",
      "source": "EVAL",
      "timestamp": "2026-11-10T09:15:02.123Z",
      "environment": "production",
      "createdAt": "2026-11-10T09:15:02.456Z",
      "updatedAt": "2026-11-10T09:15:02.456Z",
      "comment": "The answer names the right API but gives the wrong default limit.",
      "configId": null,
      "metadata": null,
      "authorUserId": null,
      "queueId": null,
      "subject": {
        "kind": "observation",
        "id": "a1b2c3d4e5f60718",
        "traceId": "7f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e"
      }
    },
    {
      "id": "c5f0c7d3-4d2f-4d8f-8a1b-2e3f4a5b6c7d",
      "projectId": "cm0proj000001",
      "name": "helpfulness",
      "dataType": "NUMERIC",
      "value": 0.75,
      "source": "API",
      "timestamp": "2026-11-10T09:14:58.000Z",
      "environment": "production",
      "createdAt": "2026-11-10T09:14:58.310Z",
      "updatedAt": "2026-11-10T09:14:58.310Z",
      "comment": null,
      "configId": "cm0cfg000042",
      "metadata": {"pipeline": "nightly"},
      "authorUserId": null,
      "queueId": null,
      "subject": {
        "kind": "trace",
        "id": "7f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e"
      }
    }
  ],
  "meta": {
    "limit": 2,
    "cursor": "eyJ0cyI6IjIwMjYtMTEtMTBUMDk6MTQ6NTguMDAwWiIsImlkIjoiYzVmMGM3ZDMifQ"
  }
}
```

Notes on the example:

- **Last page:** the same structure with `"meta": {"limit": 2}`, and no `cursor` key. [spec]
- **Cursor contents:** the cursor is opaque base64url [spec]. Its decoded content above is invented. Clients must not parse it.
- **Without `fields`:** only the ten core keys appear per object (inferred from the spec's descriptions, which tie every other field to its group; see the absent-vs-null note above).

### A.4 Docs vs spec on the score shape

**CONFLICT: DOC:scores-model describes a different, older Score object.** It lists:
- `value` (number), `stringValue` (string), `dataType` among `NUMERIC`, `CATEGORICAL`, `BOOLEAN`, `TEXT` (no `CORRECTION`), `source`, `comment`, `traceId`, `observationId`, `sessionId`, `datasetRunId`, `configId`.

The v3 read API (spec) has:
- no `stringValue`, no flat ids, `subject` instead, and `CORRECTION` in `components.schemas.ScoreDataType`.

DOC:migration#scores confirms that the v3 form is the current read shape:
- one `value` field, whose type follows `dataType`;
- `datasetRunId` is renamed `experimentId`;
- the `subject` group takes the place of the flat `traceId`/`observationId`/`sessionId`/`datasetRunId` fields of the response.

**Treat DOC:scores-model as describing the write and legacy model, not the v3 response.**

Other notes on this endpoint:

- **Row counts differ from v2.** v2 left out, without a word, any score whose trace could not be found; v3 returns all the scores that match [doc: migration#scores]. The fake should not join against traces.
- **Freshness:** reads that are not real-time may lag by around 10 minutes. v3 scores is not in the real-time list. [spec: `info.description`]

---

## B. `GET /api/public/v2/observations`

Spec location: `paths./api/public/v2/observations.get`. Its `operationId` is `observations_getMany`, its tag is `Observations`, and `security` is `BasicAuth`. [spec]

### B.1 Query parameters

[spec: `paths./api/public/v2/observations.get.parameters`]

| Name | Type (spec) | Semantics | Default |
|---|---|---|---|
| `fields` | string, nullable | Comma-separated names of field groups, any of `core`, `basic`, `time`, `io`, `metadata`, `model`, `usage`, `prompt`, `metrics`, `trace_context`; a made-up value: `io,model` | `core` and `basic` [spec] |
| `expandMetadata` | string, nullable | Comma-separated metadata keys whose values come back whole; otherwise a value longer than 200 characters is cut | none |
| `limit` | integer, nullable | The most items a page holds. A page can hold fewer, even none, and still carry `meta.cursor`. At most 1000; 50 when not given. | 50 |
| `cursor` | string, nullable | A base64 cursor for paging. (For scores the spec says base64url; here it says only base64.) | none |
| `parseIoAsJson` | boolean, nullable | **Deprecated.** `true` returns 400; I/O is always raw strings | (false) |
| `name` | string, nullable | no description in the spec; per DOC:public-api it matches the observation's name exactly | none |
| `userId` | string, nullable | no description in the spec; per DOC:public-api it matches the trace's user exactly | none |
| `sessionId` | string, nullable | filters on the session ID | none |
| `type` | string, nullable | e.g. `GENERATION`, `SPAN`, `EVENT`, `AGENT`, `TOOL`, `CHAIN`, `RETRIEVER`, `EVALUATOR`, `EMBEDDING`, `GUARDRAIL` | none |
| `traceId` | string, nullable | no description in the spec. Single value: comma-separated lists are [not stated] (unlike scores) | none |
| `level` | `ObservationLevel` enum `DEBUG` / `DEFAULT` / `WARNING` / `ERROR`, nullable | exact level | none |
| `parentObservationId` | string, nullable | Physical parent; an empty value selects only the observations that have no physical parent. | none |
| `isRootObservation` | boolean, nullable | Logical root: an observation with no physical parent, or one the SDK flagged as the app's root. An app root may have `isRootObservation=true` **and** a non-null `parentObservationId` | none |
| `environment` | **array of string** (items nullable) | matches observations whose environment is any of the values given. It is an array in the spec, so it is repeated (`environment=a&environment=b`); DOC:public-api calls it a string that may be repeated. Whether the server also accepts comma-separated values is [not stated] | none |
| `fromStartTime` | string date-time, nullable | `start_time` on or after (inclusive) | none. A request with no lower bound on the start time searches back no further than January 1, 2020. [spec description] |
| `toStartTime` | string date-time, nullable | `start_time` before (exclusive) | none |
| `version` | string, nullable | exact version | none |
| `filter` | string (JSON), nullable | See B.2 | none |

**What does not exist:**
- There is no `fromEndTime`, `tags`, `page`, or `orderBy`.
- There is no get-by-id route. For a single observation, use a `filter` on `id`. [doc: public-api, migration#observations]

**Docs recommendation:** set both `fromStartTime` and `toStartTime` on every request [doc: public-api#observations-filters]. Whether omitting them is an error is [not stated]; the spec only notes the 2020 floor.

**Sort order:** newest first, by `startTime` descending, in every case [doc: public-api#observations-pagination; doc: migration#observations]. It is not in the spec.

### B.2 `filter` grammar

[spec: `paths./api/public/v2/observations.get.parameters[name=filter].description`]

The value is a JSON string holding an array of conditions:

```
{ "type": <one of "datetime","string","number","stringOptions","categoryOptions",
            "arrayOptions","stringObject","numberObject","boolean","null">,
  "column": <column>,
  "operator": <operator valid for type>,
  "value": <any; required except for type "null">,
  "key": <string; required only for stringObject, numberObject, categoryOptions> }
```

Operators per type [spec]:

| type | operators |
|---|---|
| datetime | `>`, `<`, `>=`, `<=` |
| string | `=`, `contains`, `does not contain`, `starts with`, `ends with`, `matches` |
| stringOptions | `any of`, `none of` |
| categoryOptions | `any of`, `none of` |
| arrayOptions | `any of`, `none of`, `all of` |
| number | `=`, `>`, `<`, `>=`, `<=` |
| stringObject | `=`, `contains`, `does not contain`, `starts with`, `ends with`, `matches`, `is set`, `is not set` |
| numberObject | `=`, `>`, `<`, `>=`, `<=` |
| boolean | `=`, `<>` |
| null | `is null`, `is not null` |

Columns [spec]:

| Group | Columns |
|---|---|
| Core | `id`, `type`, `name`, `traceId`, `startTime`, `endTime`, `environment`, `level`, `statusMessage`, `version`, `userId`, `sessionId`, `isRootObservation` (boolean) |
| Trace-related | `traceName`, `traceTags` (arrayOptions), `tags` (alias for traceTags) |
| Metrics | `latency`, `timeToFirstToken`, `tokensPerSecond` |
| Usage | `inputTokens`, `outputTokens`, `totalTokens` (alias `tokens`) |
| Cost | `inputCost`, `outputCost`, `totalCost` |
| Model and prompt | `model` (alias `providedModelName`), `promptName`, `promptVersion` |
| Structured | `input`, `output`, `metadata` (with `key`) |

Rules [spec]:

- When a condition and a query parameter name the same field, the condition wins.
- A condition on `startTime` overrides `fromStartTime` and `toStartTime` alike.
- `matches` works only on `input`, `output`, and a stringObject condition on `metadata`. It ignores case on `input`/`output` and respects it on metadata values.
- A request that filters on `input` or `output` needs, among its conditions on `input`/`output`, at least one that uses `=` or `matches`.

**CONFLICTS between docs and spec on `filter`:**

- **`string` operators.**
  - DOC:public-api lists `=`, `contains`, `does not contain`, `starts with`, `ends with`, **`is not empty`**.
  - The spec lists **`matches`** instead of `is not empty`.
  - DOC:public-api separately says `input`/`output` support `=` or `matches`.
- **`metadata` condition types.**
  - DOC:public-api: a condition on `metadata` must be of type `stringObject`, and `numberObject`, `booleanObject` and `categoryOptions` are refused; a `number` condition's value must be a JSON number; a condition that breaks these rules gets a `400`.
  - The spec gives `metadata` the types `stringObject`, `numberObject` and `categoryOptions`.
- **`input`/`output` types.** DOC:public-api: these accept only the `string` type, not `stringOptions` or `null`.

### B.3 Response 200 shape

`content.application/json.schema` = `$ref: '#/components/schemas/ObservationsV2Response'` [spec]

```
ObservationsV2Response                  [spec: components.schemas.ObservationsV2Response]
  data: array of ObservationV2   (required)
  meta: ObservationsV2Meta       (required)

ObservationsV2Meta                      [spec: components.schemas.ObservationsV2Meta]
  comment: string, nullable, optional
           (sent with an empty page that still has a cursor: ask for further
            pages until no cursor comes back)
  allOf utilsCursorMetaResponse:
    cursor: string, nullable, optional (left out when there is no next page)
```

- **The list key is `data`.** [spec]
- **The pagination object is `meta`, with keys `cursor` and optionally `comment`.** There is **no `limit` in meta** here, unlike scores. [spec]
- **Next page:** pass `cursor=<meta.cursor>`. [spec, doc]
- **Last page:** `meta.cursor` is absent. [spec]. DOC:public-api#observations-pagination says to keep paging until no `cursor` comes back or `meta.cursor` is `null`, so the docs allow `null` as well.
- **Empty pages:** `data: []` with a cursor is legal and is not the end. [spec + doc]

#### ObservationV2 fields

[spec: `components.schemas.ObservationV2`]

Spec `required`: `id`, `traceId`, `startTime`, `endTime`, `projectId`, `parentObservationId`, `type`, `modelId`, `inputPrice`, `outputPrice`, `totalPrice`.

| Group | Field | Type | Nullable |
|---|---|---|---|
| core (always) | `id` | string | no |
| core | `traceId` | string | **yes** |
| core | `startTime` | string date-time | no |
| core | `endTime` | string date-time | yes |
| core | `projectId` | string | no |
| core | `parentObservationId` | string | yes |
| core | `type` | string (e.g. GENERATION, SPAN, EVENT; free string in the schema, not an enum) | no |
| basic | `name` | string | yes |
| basic | `level` | `ObservationLevel` (`DEBUG`/`DEFAULT`/`WARNING`/`ERROR`) | yes |
| basic | `statusMessage` | string | yes |
| basic | `version` | string | yes |
| basic | `environment` | string | yes |
| basic | `bookmarked` | boolean | yes |
| basic | `public` | boolean | yes |
| basic | `userId` | string | yes |
| basic | `sessionId` | string | yes |
| basic | `isRootObservation` | boolean | yes |
| time | `completionStartTime`, `createdAt`, `updatedAt` | string date-time | yes |
| io | `input` | **no type declared** (`nullable: true` only) | yes |
| io | `output` | **no type declared** | yes |
| metadata | `metadata` | object, `additionalProperties: true` | yes |
| model | `model` | string | yes |
| model | `internalModelId` | string | yes |
| model | `modelParameters` | no type declared | yes |
| usage | `usageDetails` | object of integer | yes |
| usage | `costDetails` | object of number (double) | yes |
| usage | `totalCost` | number (double) | yes |
| usage | `usagePricingTierName` | string | yes |
| prompt | `promptId`, `promptName` | string | yes |
| prompt | `promptVersion` | integer | yes |
| metrics | `latency`, `timeToFirstToken` | number (double, seconds) | yes |
| trace_context | `traceName` | string | yes |
| trace_context | `tags` | array of string | yes |
| trace_context | `release` | string | yes |
| always present (required) | `modelId` | string | yes: null unless the `model` group is asked for |
| always present (required) | `inputPrice`, `outputPrice`, `totalPrice` | **string** (decimal, e.g. `"0.0001"`) | yes: null unless `model` is requested |

Field-group membership comes from `paths./api/public/v2/observations.get.description` [spec]. DOC:public-api#field-groups agrees, except as noted below.

**Mapping the task's requested names to the spec:**

| Task's name | Spec field |
|---|---|
| parent id | `parentObservationId` |
| start time | `startTime` |
| end time | `endTime` |
| usage | `usageDetails` (+ `costDetails`, `totalCost`); there is **no `usage` field** |
| status message | `statusMessage` |
| trace name | `traceName` (trace_context group) |
| tags | `tags` (trace_context group) |
| userId, sessionId | `userId`, `sessionId` (basic group) |

**Absent vs null:** the fields of a group not asked for are **left out** of the response, not sent as `null`. The four exceptions, `modelId`, `inputPrice`, `outputPrice` and `totalPrice`, are always there but filled only when the `model` group is chosen. [doc: public-api#field-groups; doc: migration#observations]. This is consistent with the spec's `required` list.

**CONFLICT on the `usage` group.**
- DOC:public-api lists `usageDetails, inputUsage, outputUsage, totalUsage, costDetails, inputCost, outputCost, totalCost, usagePricingTierName`.
- The `ObservationV2` schema has no `inputUsage`, `outputUsage`, `totalUsage`, `inputCost` or `outputCost`.
- The spec's endpoint description lists `usageDetails, costDetails, totalCost, usagePricingTierName`.
- A fake following the spec omits the five extra keys. A client should not depend on them.

**`modelId` vs `internalModelId`.** Both exist in the spec. `internalModelId` is in the `model` group; `modelId` is always present. How they differ, beyond the descriptions (one calls it Langfuse's internal ID of the matched model, the other the matched model's ID), is [not stated].

**`metadata` truncation:** by default a value is cut at 200 characters, and `expandMetadata` brings it back whole [spec]. The exact truncation marker (ellipsis or none) is [not stated].

#### Encoding of `input` and `output`

- **Spec:** the schema declares no type, only `nullable: true`. The `parseIoAsJson` description says `input` and `output` come back as raw strings in every case.
- **Docs:** v2 hands back I/O as raw strings and does not parse it as JSON; the reader parses it where JSON is wanted. [doc: public-api#field-groups]. The migration page says the same and adds that v1 did the parsing itself. [doc: migration#observations]
- **So:** an object input arrives as a JSON document serialized into a JSON string, e.g. `"input": "{\"question\":\"hi\"}"`.
- **[not stated]:** whether a plain-text input that was ingested as a bare string arrives as `"hello"` or as the JSON-encoded `"\"hello\""`. A fake cannot be made authoritative here, so test both.

### B.4 Example response (root plus one child)

Request: `GET /api/public/v2/observations?traceId=7f1c...&fields=core,basic,time,io,metadata,model,usage,trace_context&fromStartTime=2026-11-10T00:00:00Z&toStartTime=2026-11-11T00:00:00Z&limit=50`.

- The docs say results are sorted by `startTime` desc, so the child (which starts later) comes first.
- This is the last page, so there is no `cursor` key.
- The ids, values and timestamp formatting are illustrative.

```json
{
  "data": [
    {
      "id": "a1b2c3d4e5f60718",
      "traceId": "7f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e",
      "startTime": "2026-11-10T09:15:00.250Z",
      "endTime": "2026-11-10T09:15:01.900Z",
      "projectId": "cm0proj000001",
      "parentObservationId": "0f9e8d7c6b5a4938",
      "type": "GENERATION",
      "name": "llm-answer",
      "level": "DEFAULT",
      "statusMessage": null,
      "version": null,
      "environment": "production",
      "bookmarked": false,
      "public": false,
      "userId": "user-123",
      "sessionId": "session-abc",
      "isRootObservation": false,
      "completionStartTime": "2026-11-10T09:15:00.700Z",
      "createdAt": "2026-11-10T09:15:02.000Z",
      "updatedAt": "2026-11-10T09:15:02.000Z",
      "input": "[{\"role\":\"user\",\"content\":\"What is the default page size?\"}]",
      "output": "{\"role\":\"assistant\",\"content\":\"50.\"}",
      "metadata": {"retriever": "bm25"},
      "model": "gpt-4o-mini",
      "internalModelId": "clmodel0001",
      "modelParameters": {"temperature": "0"},
      "usageDetails": {"input": 812, "output": 4, "total": 816},
      "costDetails": {"input": 0.0001218, "output": 0.0000024, "total": 0.0001242},
      "totalCost": 0.0001242,
      "usagePricingTierName": null,
      "modelId": "clmodel0001",
      "inputPrice": "0.00000015",
      "outputPrice": "0.0000006",
      "totalPrice": null,
      "traceName": "support-chat",
      "tags": ["prod", "support"],
      "release": "2026.11.1"
    },
    {
      "id": "0f9e8d7c6b5a4938",
      "traceId": "7f1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e",
      "startTime": "2026-11-10T09:15:00.000Z",
      "endTime": "2026-11-10T09:15:02.100Z",
      "projectId": "cm0proj000001",
      "parentObservationId": null,
      "type": "SPAN",
      "name": "handle-request",
      "level": "DEFAULT",
      "statusMessage": null,
      "version": null,
      "environment": "production",
      "bookmarked": false,
      "public": false,
      "userId": "user-123",
      "sessionId": "session-abc",
      "isRootObservation": true,
      "completionStartTime": null,
      "createdAt": "2026-11-10T09:15:02.200Z",
      "updatedAt": "2026-11-10T09:15:02.200Z",
      "input": "{\"message\":\"What is the default page size?\"}",
      "output": "{\"reply\":\"50.\"}",
      "metadata": {},
      "model": null,
      "internalModelId": null,
      "modelParameters": null,
      "usageDetails": {},
      "costDetails": {},
      "totalCost": null,
      "usagePricingTierName": null,
      "modelId": null,
      "inputPrice": null,
      "outputPrice": null,
      "totalPrice": null,
      "traceName": "support-chat",
      "tags": ["prod", "support"],
      "release": "2026.11.1"
    }
  ],
  "meta": {}
}
```

Notes on the example:

- **`meta: {}` on the last page.** With no cursor and no comment, `meta` is an empty object. That is consistent with the spec (both keys optional) but is not shown verbatim anywhere: [not stated].
- **Non-final pages:** `"meta": {"cursor": "<base64>"}`.
- **Empty page with a cursor:** `"meta": {"cursor": "...", "comment": "..."}`. The exact comment text is [not stated].
- **Inferred values, [not stated]:**
  - the `usageDetails` key names (`input`, `output`, `total`);
  - the `{}` vs `null` choice for a span with no usage;
  - `modelParameters` value types (the spec gives no type).
- **Default `fields`:** with no `fields`, each object has the `core` + `basic` keys plus the four always-present `modelId`/`inputPrice`/`outputPrice`/`totalPrice` keys (null).

### B.5 Behaviour notes

- **Real-time read path.** This endpoint, OTel ingestion and Metrics v2 are the only reads that happen in real time. [spec: `info.description`]
  - Data from Python SDK < 4.7.0, JS < 5.4.0, or OTel exporters without the `x-langfuse-ingestion-version: 4` header may reach the v2 endpoints as much as 15 minutes late. [doc: public-api]
  - DOC:versioning gives a delay of at most 10 minutes for the same case: **CONFLICT (10 vs 15 minutes)**.
- **Historic traces after a v4 upgrade.** A v3 trace that the upgrade converts shows up as a virtual root span of type `SPAN` whose span ID is `t-<trace_id>`. [doc: v3-to-v4#data-model]. A fake replaying migrated data should allow such ids.

---

## C. Authentication, errors, rate limits

### C.1 Authentication

- **Scheme.** `components.securitySchemes.BasicAuth = {type: http, scheme: basic}`. Both endpoints declare `security: [{BasicAuth: []}]`. [spec]
- **Credentials.** The user name is the project's Langfuse public key and the password its secret key. [spec: `info.description`, its Authentication part]
- **Exact header (standard HTTP Basic, RFC 7617):** `Authorization: Basic base64("<publicKey>:<secretKey>")`.
  - The docs show both curl's `-u` option with the two keys and an explicit `Authorization: Basic` header. [doc: public-api#quickstart]
  - The base64 construction itself follows from the RFC, not from a Langfuse page.
- **Base URLs.** [doc: public-api#base-urls]
  - EU: `https://cloud.langfuse.com/api/public`
  - US: `https://us.cloud.langfuse.com/api/public`
  - JP: `https://jp.cloud.langfuse.com/api/public`
  - HIPAA: `https://hipaa.cloud.langfuse.com/api/public`
  - Self-hosted: `<host>/api/public`.
- **Project scoping.** Keys are project-scoped (the spec sends you to the project settings for them [spec]). `GET /api/public/projects` returns the key's project as `{"data":[{"id","name","organization":{"id","name"}}]}`. [doc: public-api#quickstart]. A connector can use this to learn the `projectId` for UI URLs.

### C.2 Error responses

**Status codes declared** [spec: `paths./api/public/v3/scores.get.responses`, `paths./api/public/v2/observations.get.responses`]:

- 200, 400, 401, 403, 404, 405.
- Every non-200 has `content.application/json.schema: {}`, meaning **no body shape is declared**.
- **429 is not declared** for either endpoint.

The body shape for 401, 404 and 429 on these two endpoints is **[not stated]**. The only structured error schema in the spec is `components.schemas.PublicApiError`, and its description limits it to the stable evaluators and evaluation-rules API.

- **Its shape:** `{message: string (required), code: PublicApiErrorCode (required), details?: {issues?, retryAfterSeconds?, limit?, remaining?, resetAt?}}`.
- **Its codes include** `authentication_failed`, `resource_not_found`, `rate_limited`.
- **Do not assume** scores/observations return it. A client should parse error bodies defensively (status first, body optional).
- **For a fake:** a minimal `{"message": "..."}` is a guess, not a fact.

**When 400 is returned on these endpoints** (from parameter descriptions [spec]):
- `limit > 100` (scores);
- an unknown `fields` group (scores);
- invalid `value`/`valueMin`/`valueMax`/`dataType` combinations;
- `observationId` without `traceId`;
- `parseIoAsJson=true` (observations);
- invalid `filter` types or values (observations, [doc: public-api]).

**When 404 is returned:** for deprecated read routes on self-hosted v4 `events_only` (see F) and, per DOC:migration#scores, for the deprecated score routes, which give `404` on Langfuse v4. A 404 for an unknown trace id on the list endpoints is not described; the list endpoints filter, so presumably they return an empty `data` [not stated].

**429:**
- A bucket that runs out gets `429` answers. The `Retry-After` header then says how many seconds to wait before the next request to that bucket, and the page names that header as the value to go by. [doc: limits]
- The page's summary of a rate-limited response says the same: status 429, with the wait in seconds in `Retry-After` [doc: limits].
- The value is integer seconds (not an HTTP date), as the page gives it as a count of seconds [doc: limits].
- The 429 body is [not stated].

### C.3 Rate limits (Langfuse Cloud)

[doc: limits]

Limits are per organization and resource bucket, shared by all projects and keys. Windows are **fixed**: a window opens with the first request to its bucket, does not roll, and is not lined up with the clock or the calendar.

| Bucket | Hobby | Core | Pro/Team/Enterprise |
|---|---|---|---|
| **General API** | 30 req/min | 100 req/min | 1,000 req/min |
| Deprecated read APIs | 150 req/hour | 450 req/hour | 1,200 req/hour |
| Metrics API v2 | 100 req/day | 100 req/hour | 500 req/hour |
| Tracing (ingestion/otel) | 1,000/min | 4,000/min | 20,000/min |
| Datasets | 100/min | 200/min | 1,000/min |

Trimmed: deprecated tracing, trace data deletion, prompts and deprecated metrics rows.

**Which bucket each endpoint uses:**
- **`GET /api/public/v2/observations` → General API.** Named explicitly. [doc: limits; doc: public-api#observations-pagination]
- **`GET /api/public/v3/scores` → General API, by elimination only.** It is not named in any row; the General API row covers every public API that no other row names. [doc: limits]
- **MCP shares the General bucket.** [doc: limits]

**Other limits:**
- **Payloads:** 5MB per request and 5MB per response. [doc: limits]
- **Self-hosted:** no hard limits are set. [doc: limits; doc: public-api]

---

## D. Managed LLM-as-a-judge results as scores

**`source`:**
- The `ScoreSource` enum is `ANNOTATION`, `API`, `EVAL`. [spec]
- `CreateScoreSource` (`API`, `ANNOTATION`): its description keeps `EVAL` for scores that Langfuse's own evaluators produce, leaves it out of this write-side enum on purpose, and points readers of scores to `commons.ScoreSource`. [spec: `components.schemas.CreateScoreSource.description`]
- So managed-evaluator scores read back with `"source": "EVAL"`. This is from the spec, with one small inference: that the evaluators it means include the managed LLM-as-a-judge.
- DOC:scores-model gives `API`, `EVAL` and `ANNOTATION` as the values of `source`.

**`dataType` for categorical output:**
- The evaluator output definition `PublicEvaluatorCategoricalScore` has `dataType: const CATEGORICAL`, `categories: string[]` (two distinct values at the least), and `shouldAllowMultipleMatches: boolean` (whether one result may name several categories). [spec: `components.schemas.PublicEvaluatorCategoricalScore`]
- Judge outputs can be NUMERIC, BOOLEAN or CATEGORICAL. [spec: `PublicEvaluatorOutputDefinition`; doc: judge]
- That the resulting score carries `dataType: "CATEGORICAL"` is the natural reading, but no page states the mapping outright: [not stated, strongly implied].

**Where the category label goes:**
- On v3 reads it is `value` (a string) for `dataType: "CATEGORICAL"` [spec: `CategoricalScoreV3.properties.value`]. On the legacy shape it was `stringValue` [doc: scores-model].
- **When `shouldAllowMultipleMatches` is true, how several categories appear is [not stated].** It could be one score per category, or one joined string. **This is a real gap for a fake.**

**Where the reasoning goes:**
- The evaluator definition has `scoreReasoningInstructions`, an optional instruction on how to produce the reasoning that comes back with the score. [spec: `components.schemas.EvaluatorOutputDefinitionBase`]
- DOC:scores-model says `comment` usually holds user feedback, the reasoning of an evaluation, or internal notes.
- So the reasoning is most likely in `comment`, which v3 returns only with `fields=details`. That is **indirect**: no page says that the managed judge writes its reasoning to `comment`.
- **Score name:** what name the judge score gets (the evaluator name? configurable per rule?) is [not stated] in what I read.

**What the score is attached to:**
- An observation that matches a rule's filters sets off the evaluators attached to that rule, and each score is attached to that observation, so an evaluator gives one score per observation. [doc: judge#understanding-each-evaluation-target]
- So on v3: `subject = {"kind": "observation", "id": <observationId>, "traceId": <traceId>}`. **One subject per score**: each score refers to a single entity [doc: scores-model; doc: public-api#scores-subject]. The score is attached to the observation, not to both observation and trace.
- **Trace-level evaluators are deprecated.**
  - On Cloud they run until the v4 cutover on November 16, 2026 (2026-11-16) and give no results after it.
  - On self-hosted v4 `events_only` they produce no results. [doc: judge; doc: versioning]
  - Their scores were attached to the trace (`kind: "trace"`); that is inferred from "trace-level", [not stated] explicitly for v3.
- **Display only.** In the v4 UI, scores and comments that used to sit on a trace show on that trace's root observation [doc: v3-to-v4#ui-changes]. That is about the UI; the API `subject` for those old scores is not described as changing [not stated].
- **Experiment evaluators:** the subject kind they produce (trace of the experiment item, or `experiment`) is [not stated] in what I read.

**Side effects of judge runs:**
- Every judge execution creates its own trace in environment `langfuse-llm-as-a-judge`, with metadata `target_trace_id` and `target_observation_id`. [doc: judge]
- A connector reading observations without an environment filter will see these.

---

## E. Trace UI URL

**Pattern:** `{host}/project/{projectId}/traces/{traceId}`
- DOC:url gives one concrete example and no template. A made-up URL of the same shape: `https://cloud.langfuse.com/project/cm0proj000001/traces/7f1c2d3e-4f5a-4b6c-8d9e-0f1a2b3c4d5e`.
- The template above is read off that example; the page does not state it as a contract. The SDKs expose `get_trace_url()` (Python) and `getTraceUrl(traceId)` (JS). [doc: url]
- **`{host}` is the UI origin without `/api/public`,** e.g. `https://cloud.langfuse.com`, `https://us.cloud.langfuse.com`, or the self-hosted origin.
- **Self-hosted under a custom base path:** whether the path prefix applies is [not stated] in DOC:url.
- **Deep-linking to one observation** (a query parameter on the trace URL) is [not stated] in DOC:url.
- **`projectId` source:** the score's core `projectId` [spec: `BaseScoreV3`], the observation's core `projectId` [spec: `ObservationV2`], or `GET /api/public/projects`.

---

## F. Version differences and server version detection

**Self-hosted feature matrix** [doc: versioning, its table of which feature each version has]:

| Endpoint | OSS v2 | OSS v3 | OSS v4 |
|---|---|---|---|
| `GET /api/public/v3/scores` | Unsupported | Full | Full |
| `GET /api/public/v2/observations` (and Metrics v2) | Unsupported | **Unsupported** | Full |
| Deprecated reads (`/traces`, `/observations`, `/sessions`, `/scores`, `/v2/scores`, `/metrics`, dataset runs) | Full | Full | Unsupported |

- **Minimum v3 version for scores v3:** self-hosted Langfuse v3.179.0 or later. [doc: public-api#v3]
- **Observations v2:** self-hosted Langfuse v4 or later. [doc: public-api#v2]
- **Observations on v3:** use the v1 `GET /api/public/observations` instead. [doc: public-api]

**v4 behaviour depends on write mode** (`LANGFUSE_MIGRATION_V4_WRITE_MODE` = `legacy` / `dual` / `events_only`, default `events_only`) [doc: v3-to-v4]:

- **`events_only`** (all new deployments, and migrated ones after cutover):
  - deprecated reads return **404**: `GET /api/public/traces`, `/traces/:id`, `/observations`, `/observations/:id`, `/sessions`, `/sessions/:id`, `/scores`, `/scores/:id`, `/v2/scores`, `/v2/scores/:id`, `/metrics`, `/metrics/daily`. [doc: v3-to-v4#api-changes]
- **`legacy` or `dual`:**
  - the deprecated endpoints go on working unchanged. [doc: v3-to-v4]
  - `legacy` keeps all of v3's behaviour.
  - `dual` opens the new read path (a per-user switch in the UI, and the v2 APIs) behind `LANGFUSE_MIGRATION_V4_ALLOW_PREVIEW_OPT_IN`. [doc: v3-to-v4, env var table]
  - So on a v4 server in `legacy` mode, `/v2/observations` may not be usable. The exact response there is [not stated].

**Langfuse Cloud:**
- It always runs the latest version. [doc: versioning#cloud]
- The deprecated read endpoints are served until November 16, 2026 (2026-11-16), six days after this research date. After that, assume v4 behaviour (404) on Cloud. [doc: migration; doc: judge]

**Detecting the server version:**

- **`GET /api/public/health`** [spec: `paths./api/public/health.get`, operationId `health_health`, tag `Health`, described as a check of the API and the database].
  - **Parameters:** none in the spec (`parameters: []`). DOC:health documents an optional `?failIfDatabaseUnavailable=true`.
  - **No `security` key on the operation, and no top-level `security`.** So per the spec it needs no auth [spec].
  - **200 body:** `HealthResponse = {version: string (required, the server's version), status: string (required)}` [spec: `components.schemas.HealthResponse`].
  - **Other responses declared:** 400, 401, 403, 404, 405 with `schema: {}`, and 503 with no content.
  - **DOC:health:** 200 when the API works; 503 when it does not, or, with the parameter set, when the database cannot be reached. By default it does not check the database.
  - **Example shape:** `{"version": "<server version>", "status": "<status>"}`. The format of the `version` string (e.g. `"4.59.0"` vs `"v4.59.0"`) and the value of `status` (e.g. `"OK"`) are **[not stated]**.
- **Feature-probe alternative:**
  - Call `GET /api/public/v2/observations?limit=1&fromStartTime=...`. A 404 suggests the server lacks v2 reads (v3, or v4 in `legacy` mode).
  - The spec lists 404 among its responses, but the body for an unknown route is [not stated].
- **Self-hosted spec location:** v4.36.0+ serves its own spec at `/api/openapi.yaml` and an interactive reference at `/api/docs`. [doc: public-api; spec: `info.description`]
- **SDK minimums (useful context):** Python SDK v5 and JS/TS SDK v6 need Langfuse Cloud, or a self-hosted Langfuse at v4.59.0 or later. [doc: migration#upgrade-sdk]

---

## Gap list for the fake server (all [not stated])

1. **Error bodies.** The body shape of 400/401/404/429 on both endpoints; the spec declares `schema: {}`.
2. **`traceId` matching on scores.** Whether `traceId=T` on scores v3 also returns observation-subject scores under trace T.
3. **Score sort order** on v3.
4. **Absent vs null on scores.** Whether unrequested score field groups are absent or `null` (the docs say absent only for observations).
5. **Cursor end marker.** Whether `meta.cursor` is ever sent as `null` instead of omitted. The spec says omitted; the observations docs allow `null`.
6. **Plain-string I/O.** The encoding of plain-text `input`/`output` (bare string vs JSON-encoded string).
7. **Multi-match categorical judges.** How a judge with `shouldAllowMultipleMatches=true` stores several categories.
8. **Judge reasoning field.** That reasoning lands in `comment` is indirect evidence only. The judge score's `name` is not documented.
9. **Health values.** The format of the `version` string and the value of `status` in `/api/public/health`.
10. **Usage key names.** The keys of `usageDetails`/`costDetails`. The extra docs-only `usage` fields (`inputUsage`, `outputUsage`, `totalUsage`, `inputCost`, `outputCost`) conflict with the spec.
11. **Timestamp format.** Serialization precision and zone of `date-time` values.
