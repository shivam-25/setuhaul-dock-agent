# Lambda packaging

`build/` is generated and is not tracked: it holds vendored pip wheels for the Lambda target
(x86_64, Python 3.12), which are not this project's source. These three files are, and are copied
into the bundle by the packaging step:

| file | job |
|---|---|
| `lambda_handler.py` | Entry point. Points the service at `/tmp` and builds the working database once per cold container. |
| `build_workdb_shim.py` | Applies the same schema additions as `scripts/build_workdb.py`, against `/tmp`, without editing that script for the Lambda case. |
| `ddl.sql` | Extracted from `scripts/build_workdb.py` so both paths create identical tables. |

To rebuild the bundle, see **A5** in the AWS walkthrough, or `docs/06-AWS-RUNBOOK.md`.
