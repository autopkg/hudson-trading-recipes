# Elastic Agent

These recipes download Elastic Agent, build an installer package, and import it
into Munki. The download recipe uses the colocated
`ElasticFleetVersionResolver` processor to query Kibana's Fleet API.

## ElasticFleetVersionResolver

The processor resolves the Elastic Agent `version` and `download_url`. A recipe
can pin a specific version or use the version expected by the ECK stack.

### Input variables

| Name | Required | Description |
| --- | --- | --- |
| `KIBANA_URL` | Yes | Base URL of the Kibana instance, with no trailing slash. |
| `KIBANA_API_KEY` | No | Sent as `Authorization: ApiKey <key>`. Preferred over basic auth. |
| `KIBANA_USERNAME` | No | Basic-auth username, used when `KIBANA_API_KEY` is unset. |
| `KIBANA_PASSWORD` | No | Basic-auth password. |
| `ELASTIC_VERSION` | No | Explicit version override. Takes precedence over Kibana lookup results. |
| `ELASTIC_ARCH` | No | Agent architecture, either `aarch64` or `x86_64`. Defaults to `aarch64`. |
| `AGENT_POLICY_ID` | No | If set, use the policy's `required_versions[0].version` when available. |
| `ALLOW_SNAPSHOT` | No | If true, include `-SNAPSHOT` versions when selecting the highest version. |

### Output variables

| Name | Description |
| --- | --- |
| `version` | Resolved agent version, such as `9.3.3`. |
| `ELASTIC_VERSION` | The resolved version, also set for compatibility with child recipes. |
| `ELASTIC_ARCH` | Configured architecture, defaulting to `aarch64`. |
| `download_url` | Elastic Agent artifact base derived from the default `agent_download_sources` entry. |

### Resolution order

1. Use a non-empty `ELASTIC_VERSION` if provided.
2. If `AGENT_POLICY_ID` is set and the policy has `required_versions`, use
   `required_versions[0].version`.
3. Otherwise, select the highest version returned by
   `GET /api/fleet/agents/available_versions`. Exclude `-SNAPSHOT` versions
   unless `ALLOW_SNAPSHOT` is true.

The processor derives `download_url` from the default entry returned by
`GET /api/fleet/agent_download_sources`. If none is marked as the default, it
uses the first entry. It appends `beats/elastic-agent` to the entry's host.

### Example usage

Because the processor is in the same directory as the recipe, refer to it by
its class name:

```xml
<dict>
    <key>Processor</key>
    <string>ElasticFleetVersionResolver</string>
</dict>
<dict>
    <key>Arguments</key>
    <dict>
        <key>url</key>
        <string>%download_url%/elastic-agent-%ELASTIC_VERSION%-darwin-%ELASTIC_ARCH%.tar.gz</string>
    </dict>
    <key>Processor</key>
    <string>URLDownloader</string>
</dict>
```
