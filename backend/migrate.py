"""RGO OS — migration/entrypoint. Applies schema + stamps schema_version.
Uses DATABASE_URL (sqlite by default; postgres for production). Fails clearly
when PostgreSQL is selected without a reachable server/driver."""
import os
import sys
import db

def main():
    url = os.environ.get("DATABASE_URL")
    try:
        conn = db.connect(url)
    except RuntimeError as e:
        print(f"[migrate] BLOCKED: {e}", file=sys.stderr)
        sys.exit(3)
    print(f"[migrate] connected ({'postgres' if url and url.startswith('postgres') else 'sqlite'}); "
          f"schema_version={db.current_version(conn)}")

if __name__ == "__main__":
    main()
