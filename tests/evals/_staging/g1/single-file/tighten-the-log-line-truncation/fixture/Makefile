.PHONY: test lint

test:
	python -m doctest src/logging_util/shorten.py -v | tail -1
	python -m doctest src/logging_util/shorten.py

lint:
	python -m compileall -q src
