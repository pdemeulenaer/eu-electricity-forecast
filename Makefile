.DEFAULT_GOAL := help

PORT ?= 8000

.PHONY: help docs docs-build docs-clean

help:
	@printf '%s\n' \
		'docs        Serve the documentation at http://127.0.0.1:$(PORT)' \
		'docs-build  Build the documentation and check for warnings' \
		'docs-clean  Remove the generated site directory'

docs:
	uv run --group docs mkdocs serve -a 127.0.0.1:$(PORT)

docs-build:
	uv run --group docs mkdocs build --strict

docs-clean:
	rm -rf site
