import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

p = Path(__file__).resolve().parents[2] / 'adapters/codex/wake.py'
spec = importlib.util.spec_from_file_location('codex_wake', p)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class CodexWakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'inbox'
        self.state = Path(self.tmp.name) / 'state.json'
        self.calls = []

    def run_queue(self, argv, **kwargs):
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, 'queued', '')

    def wake(self, run=None):
        return w.wake(self.root, 'thread-id', self.state, '/bin/codex', run or self.run_queue)

    def test_empty_mailbox_does_not_wake(self):
        self.wake()
        self.assertEqual(self.calls, [])

    def test_new_ids_once_even_after_ack_rewrites(self):
        w.inbox.send(str(self.root), 'PO', 'PM-ui', 'one')
        self.wake()
        self.wake()
        w.inbox.ack(str(self.root), 'PO', ['M-1'])
        w.wake(self.root, 'thread-id', self.state, '/bin/codex', self.run_queue,
               complete=True, observed=['M-1'])
        w.inbox.send(str(self.root), 'PO', 'PM-ui', 'two')
        self.wake()
        self.assertEqual(len(self.calls), 2)
        self.assertIn('M-2', self.calls[1][5])
        self.assertEqual(self.calls[0][-1], '--resume')

    def test_messages_arriving_during_turn_are_coalesced_and_rechecked(self):
        w.inbox.send(str(self.root), 'PO', 'PM-ui', 'first')
        self.wake()
        for _ in range(5):
            w.inbox.send(str(self.root), 'PO', 'PM-ui', 'next')
            self.wake()
        self.assertEqual(len(self.calls), 1)
        w.inbox.ack(str(self.root), 'PO', ['M-1', 'M-2', 'M-3', 'M-4', 'M-5'])
        w.wake(self.root, 'thread-id', self.state, '/bin/codex', self.run_queue,
               complete=True, observed=['M-1', 'M-2', 'M-3', 'M-4', 'M-5'])
        self.assertEqual(len(self.calls), 2)
        self.assertIn('M-6', self.calls[1][5])

    def test_observed_but_unacked_is_not_requeued(self):
        w.inbox.send(str(self.root), 'PO', 'PM-ui', 'blocked')
        self.wake()
        w.wake(self.root, 'thread-id', self.state, '/bin/codex', self.run_queue,
               complete=True, observed=['M-1'])
        self.assertEqual(len(self.calls), 1)

    def test_queue_failure_does_not_consume_event(self):
        w.inbox.send(str(self.root), 'PO', 'PM-ui', 'one')
        for code, err in [(1, 'failed')]:
            with self.assertRaises(RuntimeError):
                self.wake(lambda argv, **kw: subprocess.CompletedProcess(argv, code, '', err))
            self.assertFalse(self.state.exists())
        self.wake()
        self.assertEqual(len(self.calls), 1)

    def test_resume_warning_keeps_queue_receipt_and_does_not_duplicate(self):
        w.inbox.send(str(self.root), 'PO', 'PM-ui', 'one')
        result = self.wake(lambda argv, **kw: subprocess.CompletedProcess(argv, 0, '',
            'Warning: message was queued, but could not resume: active writer'))
        self.assertIn('despertar no confirmado', result)
        self.wake()
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
