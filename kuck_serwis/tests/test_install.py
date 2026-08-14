from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from kuck_serwis import install


def _make_system_user(language):
	return frappe.get_doc(
		{
			"doctype": "User",
			"email": f"locale-{frappe.generate_hash(length=10).lower()}@example.test",
			"first_name": "Locale Test",
			"enabled": 1,
			"user_type": "System User",
			"language": language,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)


class TestInstallLifecycle(IntegrationTestCase):
	def test_after_migrate_setup_preserves_site_locale_and_user_language(self):
		user = _make_system_user("en")
		frappe.db.set_single_value("System Settings", "language", "en")
		frappe.db.set_single_value("System Settings", "number_format", "#.###,##")

		install.setup_all()
		install.setup_all()

		self.assertEqual(frappe.db.get_single_value("System Settings", "language"), "en")
		self.assertEqual(frappe.db.get_single_value("System Settings", "number_format"), "#.###,##")
		self.assertEqual(frappe.db.get_value("User", user.name, "language"), "en")
		self.assertTrue(frappe.db.exists("Role", install.ROLE))
		self.assertTrue(frappe.db.exists("Workflow", "Serwis Naprawa"))

	def test_after_install_provisions_site_default_without_overwriting_users(self):
		english_user = _make_system_user("en")
		polish_user = _make_system_user("pl")
		frappe.db.set_single_value("System Settings", "language", "de")
		frappe.db.set_single_value("System Settings", "number_format", "#,###.##")

		with (
			patch.object(install, "setup_all") as setup_all,
			patch.object(install, "seed_slowniki") as seed_slowniki,
		):
			install.after_install()

		setup_all.assert_called_once_with()
		seed_slowniki.assert_called_once_with()
		self.assertEqual(frappe.db.get_single_value("System Settings", "language"), "pl")
		self.assertEqual(frappe.db.get_single_value("System Settings", "number_format"), "#,###.##")
		self.assertEqual(frappe.db.get_value("User", english_user.name, "language"), "en")
		self.assertEqual(frappe.db.get_value("User", polish_user.name, "language"), "pl")
