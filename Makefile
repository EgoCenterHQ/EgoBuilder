# EgOS + EgoBuilder. The ISO build runs in its own work directory, out/amd64;
# the build script reads the files from the repo tree (see README.md).

VERSION := $(shell cat VERSION)
OUT     := $(CURDIR)/out

.PHONY: help check iso-amd64 clean builder

help:
	@echo "make check      syntax of every script, overlay dry run, EgoBuilder tests"
	@echo "make iso-amd64  build out/amd64/egos-$(VERSION)-amd64.iso (sudo podman, privileged debian:trixie)"
	@echo "make builder    EgoBuilder web app on http://localhost:8765 (custom images)"
	@echo "make clean      remove out/"

check:
	./tools/check.sh
	python3 -m unittest discover -s egobuilder

builder:
	python3 egobuilder/server.py

iso-amd64:
	mkdir -p $(OUT)/amd64
	sudo podman run -it --rm --privileged --network=host \
	  -v "$(OUT)/amd64:/build:Z" -v "$(CURDIR):/src:ro" -w /build debian:trixie bash -c \
	  'apt-get update && apt-get install -y live-build curl ca-certificates gnupg && (lb clean || true) && bash /src/build/amd64/EgOS4-amd64.sh 2>&1 | tee build.log'

clean:
	sudo rm -rf $(OUT)
