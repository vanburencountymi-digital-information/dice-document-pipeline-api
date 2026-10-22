from __future__ import annotations

from django.test import SimpleTestCase, override_settings
from parameterized import parameterized
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from api.client_ip import UNKNOWN_IP, client_ip, warn_if_proxy_hops_unset

CLOUDFLARE_EDGE = "104.16.0.5"
CLOUDFLARE_EDGE_V6 = "2606:4700::1"
REAL_CALLER = "9.9.9.9"


@override_settings(TRUSTED_PROXY_HOPS=1)
class ClientIPTests(SimpleTestCase):
    def _ip(self, **meta: str) -> str:
        return client_ip(Request(APIRequestFactory().get("/", **meta)))

    @parameterized.expand(
        [
            ("no_proxy_uses_remote_addr", {"REMOTE_ADDR": "1.2.3.4"}, "1.2.3.4"),
            (
                "behind_one_platform_proxy_uses_what_it_saw",
                {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": REAL_CALLER},
                REAL_CALLER,
            ),
            (
                "behind_one_platform_proxy_ignores_entries_the_caller_added",
                {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": f"6.6.6.6, {REAL_CALLER}"},
                REAL_CALLER,
            ),
            (
                "behind_one_platform_proxy_ignores_a_faked_cloudflare_header",
                {
                    "REMOTE_ADDR": "10.0.0.1",
                    "HTTP_X_FORWARDED_FOR": REAL_CALLER,
                    "HTTP_CF_CONNECTING_IP": "6.6.6.6",
                },
                REAL_CALLER,
            ),
            (
                "through_cloudflare_uses_its_header",
                {
                    "REMOTE_ADDR": "10.0.0.1",
                    "HTTP_X_FORWARDED_FOR": f"{REAL_CALLER}, {CLOUDFLARE_EDGE}",
                    "HTTP_CF_CONNECTING_IP": REAL_CALLER,
                },
                REAL_CALLER,
            ),
            (
                "ipv6_caller_is_identified_by_its_64",
                {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "2001:db8:1:2:aaaa::7"},
                "2001:db8:1:2::",
            ),
            (
                "ipv4_in_ipv6_is_treated_as_ipv4",
                {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": "::ffff:9.9.9.9"},
                "9.9.9.9",
            ),
            (
                "through_cloudflare_ipv6_edge",
                {
                    "REMOTE_ADDR": "10.0.0.1",
                    "HTTP_X_FORWARDED_FOR": CLOUDFLARE_EDGE_V6,
                    "HTTP_CF_CONNECTING_IP": REAL_CALLER,
                },
                REAL_CALLER,
            ),
            (
                "through_cloudflare_ipv6_caller_is_identified_by_its_64",
                {
                    "REMOTE_ADDR": "10.0.0.1",
                    "HTTP_X_FORWARDED_FOR": CLOUDFLARE_EDGE,
                    "HTTP_CF_CONNECTING_IP": "2001:db8:1:2:bbbb::9",
                },
                "2001:db8:1:2::",
            ),
            (
                "through_cloudflare_without_its_header_falls_back_to_the_edge",
                {"REMOTE_ADDR": "10.0.0.1", "HTTP_X_FORWARDED_FOR": CLOUDFLARE_EDGE},
                CLOUDFLARE_EDGE,
            ),
            (
                "through_cloudflare_with_a_junk_header_falls_back_to_the_edge",
                {
                    "REMOTE_ADDR": "10.0.0.1",
                    "HTTP_X_FORWARDED_FOR": CLOUDFLARE_EDGE,
                    "HTTP_CF_CONNECTING_IP": "x" * 5000,
                },
                CLOUDFLARE_EDGE,
            ),
            (
                "junk_forwarded_entry_falls_back_to_remote_addr",
                {"REMOTE_ADDR": "1.2.3.4", "HTTP_X_FORWARDED_FOR": "not-an-ip"},
                "1.2.3.4",
            ),
            ("test_factory_default_is_localhost", {}, "127.0.0.1"),
            ("empty_remote_addr_is_unknown", {"REMOTE_ADDR": ""}, UNKNOWN_IP),
            ("junk_remote_addr_is_unknown", {"REMOTE_ADDR": "not-an-ip"}, UNKNOWN_IP),
        ]
    )
    def test_finds_the_real_caller(self, _label: str, meta: dict, expected: str) -> None:
        self.assertEqual(self._ip(**meta), expected)

    @override_settings(TRUSTED_PROXY_HOPS=2)
    def test_skips_extra_platform_entries(self) -> None:
        ip = self._ip(HTTP_X_FORWARDED_FOR=f"6.6.6.6, {REAL_CALLER}, 130.211.0.1")

        self.assertEqual(ip, REAL_CALLER)

    @override_settings(TRUSTED_PROXY_HOPS=0)
    def test_zero_hops_ignores_the_forwarded_header(self) -> None:
        ip = self._ip(REMOTE_ADDR="1.2.3.4", HTTP_X_FORWARDED_FOR=REAL_CALLER)

        self.assertEqual(ip, "1.2.3.4")

    def test_two_ipv6_addresses_in_one_64_are_one_caller_and_other_64s_are_not(self) -> None:
        first = self._ip(HTTP_X_FORWARDED_FOR="2001:db8:1:2::1")
        second = self._ip(HTTP_X_FORWARDED_FOR="2001:db8:1:2:ffff:ffff:ffff:ffff")
        other = self._ip(HTTP_X_FORWARDED_FOR="2001:db8:1:3::1")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_result_is_always_short_enough_for_a_database_key(self) -> None:
        ip = self._ip(HTTP_X_FORWARDED_FOR="a" * 100_000)

        self.assertLessEqual(len(ip), 45)


class ClientIPDefaultTests(SimpleTestCase):
    def test_an_unconfigured_deployment_ignores_the_forwarded_header(self) -> None:
        request = Request(
            APIRequestFactory().get("/", REMOTE_ADDR="1.2.3.4", HTTP_X_FORWARDED_FOR="6.6.6.6")
        )

        self.assertEqual(client_ip(request), "1.2.3.4")


class ProxyHopsWarningTests(SimpleTestCase):
    @override_settings(DEBUG=False, TRUSTED_PROXY_HOPS=0)
    def test_warns_when_not_debugging_and_hops_is_zero(self) -> None:
        with self.assertLogs("api.client_ip", level="WARNING"):
            warn_if_proxy_hops_unset()

    @override_settings(DEBUG=False, TRUSTED_PROXY_HOPS=1)
    def test_quiet_when_hops_is_set(self) -> None:
        with self.assertNoLogs("api.client_ip", level="WARNING"):
            warn_if_proxy_hops_unset()

    @override_settings(DEBUG=True, TRUSTED_PROXY_HOPS=0)
    def test_quiet_in_local_development(self) -> None:
        with self.assertNoLogs("api.client_ip", level="WARNING"):
            warn_if_proxy_hops_unset()
