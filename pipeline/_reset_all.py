"""
RESET_ALL helper -- called by RESET_ALL.bat.
Full clean: deletes CSV, clears remediated/ and logs/.
"""
import shutil
from pathlib import Path

rem_dir  = Path("remediation_work/remediated")
log_dir  = Path("remediation_work/logs")
csv_file = Path("remediation_status.csv")

# Clear remediated folder
if rem_dir.exists():
    count = sum(1 for _ in rem_dir.glob("*"))
    shutil.rmtree(rem_dir)
    rem_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Cleared remediated/  ({count} file(s) removed)")
else:
    rem_dir.mkdir(parents=True, exist_ok=True)
    print("  remediated/ created (was empty)")

# Clear logs folder
if log_dir.exists():
    count = sum(1 for _ in log_dir.glob("*"))
    shutil.rmtree(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"  Cleared logs/  ({count} file(s) removed)")
else:
    log_dir.mkdir(parents=True, exist_ok=True)
    print("  logs/ created (was empty)")

# Delete the status CSV entirely -- it will be rebuilt on next run
if csv_file.exists():
    csv_file.unlink()
    print(f"  Deleted:  {csv_file}")
else:
    print("  No CSV found -- nothing to delete")

# Verify downloads folder is intact
dl_dir = Path("remediation_work/downloads")
if dl_dir.exists():
    count = sum(1 for _ in dl_dir.glob("*.pdf"))
    print(f"  Downloads folder intact: {count} PDF(s) present")
else:
    print("  WARNING: downloads/ folder not found")

print()
print("  Full reset complete.")
print("  Run RUN_PIPELINE.bat to start both passes from scratch.")
