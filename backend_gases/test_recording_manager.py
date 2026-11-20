"""Prueba sin cámara: FFmpeg simulado para validar ciclo de vida y metadatos."""
import asyncio
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import patch

from recording_manager import RecordingManager, RecordingError

FAKE_FFMPEG = r'''#!/usr/bin/env python3
import os, sys
output = sys.argv[-1]
with open(output, 'wb') as file:
    file.write(b'FAKE_MP4_ONLY_FOR_LIFECYCLE_TEST' * 150)
sys.stdin.readline()
'''


class RecordingTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_stop_saves_record_and_ticket(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = Path(d) / 'bin'; bin_dir.mkdir()
            script = bin_dir / 'ffmpeg'; script.write_text(FAKE_FFMPEG)
            script.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(bin_dir) + ':' + os.environ['PATH']}):
                m = RecordingManager(Path(d)/'video', lambda: 'rtsp://example.com/live', min_free_mb=128)
                active = await m.start()
                self.assertTrue(m.current()['recording'])
                self.assertEqual(active['status'], 'grabando')
                with self.assertRaises(RecordingError):
                    await m.start()
                completed = await m.stop(active['id'])
                self.assertEqual(completed['status'], 'finalizado')
                self.assertTrue((m.folder / completed['filename']).exists())
                self.assertFalse(m.current()['recording'])
                ticket = m.issue_ticket(active['id'])
                self.assertIsNotNone(m.video_file(active['id'], ticket))
                self.assertIsNone(m.video_file(active['id'], 'bad'))
                self.assertEqual(m.list_records(10)[0]['id'], active['id'])
                m2 = RecordingManager(m.folder, lambda: 'rtsp://example.com/live')
                self.assertEqual(m2.list_records(10)[0]['status'], 'finalizado')
                await m.shutdown()

    async def test_unlimited_rondin_ignores_elapsed_hours(self):
        with tempfile.TemporaryDirectory() as d:
            bin_dir = Path(d) / 'bin'; bin_dir.mkdir()
            script = bin_dir / 'ffmpeg'; script.write_text(FAKE_FFMPEG)
            script.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(bin_dir) + ':' + os.environ['PATH']}):
                m = RecordingManager(Path(d)/'video', lambda: 'rtsp://example.com/live',
                                     max_minutes=0, min_free_mb=128)
                self.assertIsNone(m.max_seconds)
                active = await m.start()
                m.active['started_at'] = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
                await asyncio.sleep(2.3)  # Da oportunidad al monitor de comprobar el tiempo.
                self.assertTrue(m.current()['recording'], 'No debe parar a las 4 horas')
                finished = await m.stop(active['id'])
                self.assertEqual(finished['status'], 'finalizado')
                self.assertGreaterEqual(finished['duration_seconds'], 4 * 3600)
                await m.shutdown()

    def test_optional_limit_is_still_supported(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(RecordingManager(Path(d)/'video', lambda: 'rtsp://demo').max_seconds)
            self.assertEqual(RecordingManager(Path(d)/'other', lambda: 'rtsp://demo', max_minutes=10).max_seconds, 600)



if __name__ == '__main__':
    unittest.main()
