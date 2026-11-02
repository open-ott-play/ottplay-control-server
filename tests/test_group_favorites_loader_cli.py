"""Concurrent group capability reads must share fully initialized Favorites types."""

import importlib.machinery
import importlib.util
from pathlib import Path
import queue
import sys
import threading
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "ott_group_favorites_loader", Path(__file__).resolve().parents[1] / "cli" / "ott.py")
ott = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ott)

TARGET = "favorites_protocol"
DEPENDENCIES = ("favorites_document", "preset_favorites", "favorites_transfer", TARGET)


class GroupFavoritesLoaderTest(unittest.TestCase):
    def setUp(self):
        saved = {name: sys.modules.pop(name) for name in DEPENDENCIES if name in sys.modules}

        def restore():
            for name in DEPENDENCIES:
                sys.modules.pop(name, None)
            sys.modules.update(saved)

        self.addCleanup(restore)

    def test_concurrent_load_waits_for_initialization_and_shares_result_class(self):
        original = importlib.machinery.SourceFileLoader.exec_module
        lock = ott.FAVORITES_MODULE_LOCK
        published, release = threading.Event(), threading.Event()
        second_finished = threading.Event()
        notifications = queue.Queue()
        modules, errors, loaded = {}, [], []
        second = None

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread() is second:
                    # Observe actual lock contention, rather than assuming the
                    # second thread ran because a short sleep has elapsed.
                    acquired = lock.acquire(blocking=False)
                    if acquired:
                        lock.release()
                    notifications.put("unlocked" if acquired else "blocked")
                lock.acquire()
                return self

            def __exit__(self, *_args):
                lock.release()

        def delayed_initialize(loader, module):
            loaded.append(module.__name__)
            if module.__name__ == TARGET:
                published.set()
                if not release.wait(5):
                    raise AssertionError("initialization barrier was not released")
            return original(loader, module)

        def load(label):
            try:
                modules[label] = ott.favorites_module(TARGET)
                if label == "second":
                    notifications.put("returned")
            except BaseException as error:
                errors.append(error)
                if label == "second":
                    notifications.put("failed")
            finally:
                if label == "second":
                    second_finished.set()

        first = threading.Thread(target=load, args=("first",), daemon=True)
        second = threading.Thread(target=load, args=("second",), daemon=True)
        with mock.patch.object(ott, "FAVORITES_MODULE_LOCK", ObservedLock()), \
                mock.patch.object(importlib.machinery.SourceFileLoader, "exec_module", delayed_initialize):
            first.start()
            try:
                self.assertTrue(published.wait(5), "first loader did not publish its placeholder")
                self.assertFalse(hasattr(sys.modules[TARGET], "ValidatedResult"))
                second.start()
                self.assertEqual(notifications.get(timeout=5), "blocked",
                                 "second loader returned a partially initialized module")
                self.assertFalse(second_finished.is_set())
            finally:
                release.set()
                first.join(5)
                if second.ident is not None:
                    second.join(5)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertIs(modules["first"], modules["second"])
        self.assertIs(modules["first"].ValidatedResult, modules["second"].ValidatedResult)
        self.assertIs(modules["first"], sys.modules[TARGET])
        self.assertEqual(loaded.count(TARGET), 1)

    def test_failed_or_interrupted_initialization_removes_only_partial_module(self):
        original = importlib.machinery.SourceFileLoader.exec_module
        for exception, expected in ((RuntimeError("private fixture detail"), ott.Error),
                                    (KeyboardInterrupt(), KeyboardInterrupt),
                                    (SystemExit(7), SystemExit)):
            with self.subTest(exception=type(exception).__name__):
                sys.modules.pop(TARGET, None)

                def fail_initialize(loader, module, exception=exception):
                    if module.__name__ == TARGET:
                        self.assertIs(sys.modules[TARGET], module)
                        raise exception
                    return original(loader, module)

                with mock.patch.object(importlib.machinery.SourceFileLoader, "exec_module", fail_initialize):
                    with self.assertRaises(expected) as failure:
                        ott.favorites_module(TARGET)
                self.assertNotIn("private fixture detail", str(failure.exception))
                self.assertNotIn(TARGET, sys.modules)
                self.assertTrue(all(name in sys.modules for name in DEPENDENCIES[:-1]))
                complete = ott.favorites_module(TARGET)
                self.assertTrue(hasattr(complete, "ValidatedResult"))
                self.assertIs(complete, sys.modules[TARGET])


if __name__ == "__main__":
    unittest.main()
