import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class ReleaseCopyTests(unittest.TestCase):
    def test_private_source_does_not_make_release_unreadable_or_copy_secrets(self):
        script=Path(__file__).resolve().parents[1]/'scripts/install.sh'
        lines=script.read_text().splitlines()
        index=next(i for i,line in enumerate(lines) if line.startswith('tar -C '))
        copy_step=lines[index]
        if lines[index+1].startswith('chmod 0755 '):copy_step+='\n'+lines[index+1]
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source';source.mkdir(mode=0o700)
            release=Path(folder)/'release';release.mkdir(mode=0o755)
            (source/'main.py').write_text('pass\n')
            (source/'.env').write_text('SECRET=private')
            (source/'private-mode.txt').write_text('preserve permissions')
            (source/'private-mode.txt').chmod(0o600)
            env={**os.environ,'CREW_SOURCE':str(source),'CREW_RELEASE':str(release)}
            subprocess.run(['bash','-euo','pipefail','-c',copy_step],env=env,check=True)
            self.assertEqual(release.stat().st_mode & 0o777,0o755)
            self.assertEqual(source.stat().st_mode & 0o777,0o700)
            self.assertEqual((release/'private-mode.txt').stat().st_mode & 0o777,0o600)
            self.assertFalse((release/'.env').exists())
