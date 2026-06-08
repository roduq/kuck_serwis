app_name = "kuck_serwis"
app_title = "Kuck Serwis"
app_publisher = "Kuck"
app_description = "Kuck Serwis module"
app_email = "crazyponkeymen@gmail.com"
app_license = "mit"

# Apps
# ------------------

# Klient naprawy to ERPNext Customer — moduł wymaga zainstalowanego ERPNext (version-16).
required_apps = ["erpnext"]

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "kuck_serwis",
# 		"logo": "/assets/kuck_serwis/logo.png",
# 		"title": "Kuck Serwis",
# 		"route": "/kuck_serwis",
# 		"has_permission": "kuck_serwis.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/kuck_serwis/css/kuck_serwis.css"
# app_include_js = "/assets/kuck_serwis/js/kuck_serwis.js"

# include js, css files in header of web template
# web_include_css = "/assets/kuck_serwis/css/kuck_serwis.css"
# web_include_js = "/assets/kuck_serwis/js/kuck_serwis.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "kuck_serwis/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "kuck_serwis/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# Number Card / Dashboard Chart nie są w domyślnym zestawie synchronizowanym przez
# `bench migrate` — rejestrujemy je tu, by pliki pulpitu (number_card/, dashboard_chart/)
# wczytywały się tak samo jak Report czy Print Format.
importable_doctypes = ["Number Card", "Dashboard Chart"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "kuck_serwis.utils.jinja_methods",
# 	"filters": "kuck_serwis.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "kuck_serwis.install.before_install"
after_install = "kuck_serwis.install.after_install"

# Konfiguracja modułu jest idempotentna — odtwarzamy ją po każdej migracji, żeby uprawnienia
# roli Serwis (także do doctypów ERPNext) przetrwały aktualizacje i resynchronizację.
after_migrate = "kuck_serwis.install.setup_all"

# Uninstallation
# ------------

# before_uninstall = "kuck_serwis.uninstall.before_uninstall"
# after_uninstall = "kuck_serwis.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "kuck_serwis.utils.before_app_install"
# after_app_install = "kuck_serwis.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "kuck_serwis.utils.before_app_uninstall"
# after_app_uninstall = "kuck_serwis.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "kuck_serwis.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "kuck_serwis.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"kuck_serwis.tasks.all"
# 	],
# 	"daily": [
# 		"kuck_serwis.tasks.daily"
# 	],
# 	"hourly": [
# 		"kuck_serwis.tasks.hourly"
# 	],
# 	"weekly": [
# 		"kuck_serwis.tasks.weekly"
# 	],
# 	"monthly": [
# 		"kuck_serwis.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "kuck_serwis.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "kuck_serwis.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "kuck_serwis.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "kuck_serwis.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["kuck_serwis.utils.before_request"]
# after_request = ["kuck_serwis.utils.after_request"]

# Job Events
# ----------
# before_job = ["kuck_serwis.utils.before_job"]
# after_job = ["kuck_serwis.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"kuck_serwis.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

