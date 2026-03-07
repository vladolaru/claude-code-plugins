IMAGE := yoloing-safe-e2e
AUTH_VOL := claude-auth
DOCKER_CONTEXT := ../../../..
DOCKERFILE := Dockerfile
HOME_DIR := /Users/testuser

.PHONY: build auth run run-save shell rebuild clean clean-all

## Build the e2e test image
build:
	docker build -t $(IMAGE) -f $(DOCKERFILE) $(DOCKER_CONTEXT)

## Authenticate Claude Code (one-time, interactive)
auth:
	docker run -it --entrypoint bash -v $(AUTH_VOL):$(HOME_DIR)/.claude $(IMAGE)

## Run the full e2e test suite
run:
	docker run --rm -v $(AUTH_VOL):$(HOME_DIR)/.claude $(IMAGE)

## Run tests and save results to ./results on host
run-save:
	mkdir -p results
	docker run --rm \
		-v $(AUTH_VOL):$(HOME_DIR)/.claude \
		-v $(PWD)/results:$(HOME_DIR)/results \
		$(IMAGE)

## Interactive shell for debugging
shell:
	docker run -it --entrypoint bash -v $(AUTH_VOL):$(HOME_DIR)/.claude $(IMAGE)

## Rebuild image (auth volume survives)
rebuild: build

## Remove image and results (auth volume kept)
clean:
	docker rmi $(IMAGE) 2>/dev/null || true
	rm -rf results

## Remove everything: image, results, and auth volume (requires re-login)
clean-all: clean
	docker volume rm $(AUTH_VOL) 2>/dev/null || true

## Show available targets
help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  build      Build the Docker image (run from this directory)"
	@echo "  auth       Interactive shell to run 'claude auth login' (one-time)"
	@echo "  run        Run full e2e suite (22 tests, ~15-30 min, real API calls)"
	@echo "  run-save   Run tests and save results to ./results/"
	@echo "  shell      Interactive shell for debugging"
	@echo "  rebuild    Rebuild image after code changes (auth survives)"
	@echo "  clean      Remove image and local results (auth volume kept)"
	@echo "  clean-all  Remove everything including auth volume (requires re-login)"
