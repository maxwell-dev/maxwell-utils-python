prj-dir := $(shell pwd)
src-dir := $(prj-dir)
venv-dir := $(prj-dir)/venv
python-native := python3
python := $(venv-dir)/bin/python
pytest := $(venv-dir)/bin/pytest
pip := $(venv-dir)/bin/pip
pip-compile := $(venv-dir)/bin/pip-compile

define get_site_dir
$(shell $(python) -c "import sysconfig; print(sysconfig.get_path(\"purelib\"))")
endef

prepare-dev: create-env create-dirs install-build-tools install-dev-deps set-path

prepare-prod: create-env create-dirs install-build-tools install-prod-deps set-path

create-env:
	$(python-native) -m venv $(venv-dir)

create-dirs:
	mkdir -p log

install-build-tools:
	$(pip) install --upgrade pip
	$(pip) install pip-tools

install-prod-deps:
	$(pip-compile) --strip-extras -v
	$(pip) install -r requirements.txt

install-dev-deps:
	$(pip-compile) --strip-extras -v
	$(pip) install -e .[test]

set-path:
	echo $(src-dir) > $(call get_site_dir)/my.pth

run:
	$(python) $(filter-out $@, $(MAKECMDGOALS))

pytest:
	$(pytest) --cov=./ test/

publish:
	$(python) -m build && twine check dist/* && twine upload -r pypi dist/*

publish-test:
	$(python) -m build && twine check dist/* && twine upload -r pypitest dist/*

clean:
	rm -rf $(venv-dir) 
	rm -rf $(prj-dir)/build $(prj-dir)/dist ${prj-dir}/maxwell/maxwell_utils.egg-info
	rm -rf $(prj-dir)/examples/__pycache__ 
	rm -rf $(prj-dir)/maxwell/__pycache__ $(prj-dir)/maxwell/utils/__pycache__
	rm -rf $(prj-dir)/test/__pycache__
	rm -rf $(prj-dir)/.pytest_cache
	rm -rf log