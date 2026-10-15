from unittest.mock import create_autospec, patch

from django.db import OperationalError
from django.test import SimpleTestCase
from parameterized import parameterized

from common.db import drop_connection_if_safe, retry_on_lost_connection


def _target(*args: object, **kwargs: object) -> object:
    """Stand-in for a repeatable write; autospec'd so the mock is a real function the
    decorator can read `__qualname__` from.
    """
    return None


# No autospec: `django.db.connection` is a lazy proxy, so its spec has no `in_atomic_block`.
@patch("common.db.connection")
class DropConnectionIfSafeTests(SimpleTestCase):
    @parameterized.expand(
        [
            ("outside_transaction_closes_connection", False, True),
            ("inside_transaction_leaves_connection_open", True, False),
        ]
    )
    def test_closes_connection_only_outside_a_transaction(
        self, mock_connection, _name, in_atomic_block, expect_closed
    ) -> None:
        mock_connection.in_atomic_block = in_atomic_block

        result = drop_connection_if_safe()

        self.assertEqual(result, expect_closed)
        self.assertEqual(mock_connection.close.called, expect_closed)


# No autospec: `django.db.connection` is a lazy proxy, so its spec has no `in_atomic_block`.
@patch("common.db.connection")
class RetryOnLostConnectionTests(SimpleTestCase):
    def test_returns_result_without_retry_when_call_succeeds(self, mock_connection) -> None:
        func = create_autospec(_target, return_value="ok")

        result = retry_on_lost_connection(func)("a", key="b")

        self.assertEqual(result, "ok")
        func.assert_called_once_with("a", key="b")
        mock_connection.close.assert_not_called()

    def test_retries_once_on_fresh_connection_after_operational_error(
        self, mock_connection
    ) -> None:
        mock_connection.in_atomic_block = False
        func = create_autospec(_target, side_effect=[OperationalError("lost"), "ok"])

        result = retry_on_lost_connection(func)()

        self.assertEqual(result, "ok")
        self.assertEqual(func.call_count, 2)
        mock_connection.close.assert_called_once_with()

    def test_raises_when_retry_also_fails(self, mock_connection) -> None:
        mock_connection.in_atomic_block = False
        func = create_autospec(_target, side_effect=OperationalError("lost"))

        with self.assertRaises(OperationalError):
            retry_on_lost_connection(func)()

        self.assertEqual(func.call_count, 2)

    def test_does_not_retry_inside_a_transaction(self, mock_connection) -> None:
        mock_connection.in_atomic_block = True
        func = create_autospec(_target, side_effect=OperationalError("lost"))

        with self.assertRaises(OperationalError):
            retry_on_lost_connection(func)()

        func.assert_called_once()
        mock_connection.close.assert_not_called()
