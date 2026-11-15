PYTHON  ?= python3
REGION  ?= asia-south1
PY_SERVICES := libs/warehouse_common services/order-intake services/inventory services/picking services/packing services/analytics-api services/dispatch-function

.PHONY: help install console proto dashboards diagrams design-pdf test test-unit test-integration test-flink test-terraform test-manifests \
        local-up local-down smoke load bootstrap backend killswitch-check flink-jar preflight up down grafana

help:
	@echo "No Google Cloud needed:"
	@echo "  make install           install Python packages into the active environment"
	@echo "  make test              run every test that works without Google Cloud"
	@echo "  make console           preview the Warehouse Console with a fake API, on :8099"
	@echo "  make local-up          start the whole system in Docker Compose"
	@echo "  make smoke             send one order through the system"
	@echo "  make load              run the k6 load test"
	@echo "  make local-down        stop Docker Compose"
	@echo "Google Cloud:"
	@echo "  make bootstrap         one-time project setup, including the budget and kill switch"
	@echo "  make killswitch-check  confirm the kill switch could turn billing off"
	@echo "  make flink-jar         build the Flink job and upload it"
	@echo "  make preflight         check the project has the vCPUs the stack needs"
	@echo "  make up                create everything billed by the hour"
	@echo "  make grafana           print Grafana's public address and admin password"
	@echo "  make down              destroy everything billed by the hour"

install:
	$(PYTHON) -m pip install -e 'libs/warehouse_common[dev]'

# The Warehouse Console page with a fake API, for working on the page without the cloud.
console:
	$(PYTHON) scripts/console_dev.py

proto:
	PYTHON=$(PYTHON) ./scripts/gen_proto.sh

dashboards:
	$(PYTHON) scripts/gen_dashboards.py

diagrams:
	$(PYTHON) scripts/gen_diagrams.py

design-pdf: diagrams
	$(PYTHON) scripts/build_design_pdf.py

test: test-unit test-flink test-terraform test-manifests test-integration

test-unit:
	@for dir in $(PY_SERVICES); do \
		echo "== $$dir"; \
		(cd $$dir && $(PYTHON) -m pytest tests -q) || exit 1; \
	done
	$(PYTHON) -m pytest tests/scripts -q

# Needs ZooKeeper and PostgreSQL: docker compose up -d zookeeper-1 zookeeper-2 zookeeper-3 postgres
test-integration:
	ZK_HOSTS=$${ZK_HOSTS:-localhost:2181} \
	DB_DSN=$${DB_DSN:-postgresql://warehouse:warehouse@localhost:5432/warehouse} \
	$(PYTHON) -m pytest tests/integration -q

test-flink:
	cd services/analytics-flink && mvn -q -B package

# Plans both stacks against mocked providers. Needs no Google Cloud project.
test-terraform:
	terraform -chdir=infra/bootstrap init -backend=false -input=false > /dev/null
	terraform -chdir=infra/bootstrap validate
	terraform -chdir=infra/bootstrap test
	terraform -chdir=infra/runtime init -backend=false -input=false > /dev/null
	terraform -chdir=infra/runtime validate
	terraform -chdir=infra/runtime test

test-manifests:
	kustomize build gitops/services/overlays/prod | kubeconform -strict -summary -kubernetes-version 1.36.0 -ignore-missing-schemas
	kustomize build gitops/platform/zookeeper | kubeconform -strict -summary -kubernetes-version 1.36.0 -ignore-missing-schemas
	helm template root gitops/apps | kubeconform -strict -summary -kubernetes-version 1.36.0 -ignore-missing-schemas

local-up:
	docker compose up --build -d

local-down:
	docker compose down -v

smoke:
	BASE_URL=$${BASE_URL:-http://localhost:8080} ./scripts/smoke.sh

load:
	k6 run -e BASE_URL=$${BASE_URL:-http://localhost:8080} load-tests/orders.js

# Terraform needs these APIs on before it can turn on the rest.
bootstrap:
	gcloud services enable cloudresourcemanager.googleapis.com serviceusage.googleapis.com \
		cloudbilling.googleapis.com billingbudgets.googleapis.com
	terraform -chdir=infra/bootstrap init
	terraform -chdir=infra/bootstrap apply
	$(MAKE) backend

backend:
	@printf 'bucket = "%s"\nprefix = "runtime"\n' \
		"$$(terraform -chdir=infra/bootstrap output -raw state_bucket)" > infra/runtime/backend.hcl
	@echo "wrote infra/runtime/backend.hcl"

# Sends the kill switch a self-test. It reports whether it could turn billing off,
# without doing so.
killswitch-check:
	gcloud pubsub topics publish budget-alerts --attribute=check=true \
		--message='{"costAmount": 0, "budgetAmount": 1}'
	@echo "Wait a minute, then read the answer with:"
	@echo "  gcloud logging read 'jsonPayload.message=\"self-test\"' --limit 1 --freshness 10m"

flink-jar:
	cd services/analytics-flink && mvn -q -B -DskipTests package
	gcloud storage cp services/analytics-flink/target/analytics-flink-1.0.0.jar \
		gs://$$(terraform -chdir=infra/bootstrap output -raw artifacts_bucket)/flink/analytics-flink.jar

preflight:
	REGION=$(REGION) ./scripts/preflight.sh

up:
	./scripts/up.sh

down:
	./scripts/down.sh

# Grafana's public address and admin password.
grafana:
	@echo "Grafana:  http://$$(kubectl -n monitoring get service monitoring-grafana -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
	@echo "User:     admin"
	@echo "Password: $$(terraform -chdir=infra/runtime output -raw grafana_admin_password)"
