"""Compile and run the process-runner timeout/stderr checks."""
from pathlib import Path
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='switcher-process-check-') as temp:
    executable = Path(temp)/'ProcessTimeoutChecks'
    subprocess.run(['/usr/bin/swiftc','-parse-as-library','-D','SWITCHER_MODEL_CHECKS',
                    str(repo/'app/App.swift'),str(repo/'tests/ProcessTimeoutChecks.swift'),
                    '-o',str(executable)],check=True)
    subprocess.run([str(executable)],check=True)
