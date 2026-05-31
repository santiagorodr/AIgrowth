# ══════════════════════════════════════════════════════════════
# Elempleo AI Growth Engine — Makefile
# ══════════════════════════════════════════════════════════════
# Stack cloud (sin Docker):
#   PostgreSQL → Supabase (cloud, free tier)
#   Qdrant     → Qdrant Cloud (cloud, free tier)
#
# Comandos principales:
#   make init        — Setup inicial (instala deps + carga datos)
#   make setup-cloud — Re-indexa datos en Qdrant Cloud
#   make load-data   — Carga vacantes y usuarios mock
#   make test        — Health check del stack
#   make gateway-dev — Levanta el LLM Gateway (proceso Python)
# ══════════════════════════════════════════════════════════════

.PHONY: init setup-cloud load-data test install gateway-dev

# ── Colores para output ────────────────────────────────────────
BLUE  := \033[34m
GREEN := \033[32m
RESET := \033[0m

## ── Setup ─────────────────────────────────────────────────────

init: ## Setup inicial: instala deps y carga datos mock en Supabase + Qdrant Cloud
	@echo "$(BLUE)══════════════════════════════════════$(RESET)"
	@echo "$(BLUE)  Elempleo AI Growth — Setup inicial$(RESET)"
	@echo "$(BLUE)══════════════════════════════════════$(RESET)"
	@echo ""
	@if [ ! -f .env ]; then \
		echo "$(BLUE)▶ Creando .env desde .env.example...$(RESET)"; \
		cp .env.example .env; \
		echo "$(GREEN)✅ .env creado$(RESET)"; \
		echo "⚠️  IMPORTANTE: Edita .env con tus credenciales de Supabase, Qdrant Cloud y Anthropic"; \
		echo "   Abre: nano .env"; \
		echo ""; \
	fi
	$(MAKE) install
	$(MAKE) setup-cloud
	@echo ""
	@echo "$(GREEN)════════════════════════════════════════$(RESET)"
	@echo "$(GREEN)  ✅ Setup completado$(RESET)"
	@echo "$(GREEN)════════════════════════════════════════$(RESET)"
	@echo ""
	@echo "  Próximos pasos:"
	@echo "  1. make test        → Verificar conexiones cloud"
	@echo "  2. make gateway-dev → Levantar el LLM Gateway"
	@echo "  3. make demo-job-match → Probar el primer agente"
	@echo ""

setup-cloud: ## Crea colecciones en Qdrant Cloud y carga todos los datos mock
	@echo "$(BLUE)▶ Inicializando Qdrant Cloud...$(RESET)"
	python3 -m vector_db.setup
	@echo "$(BLUE)▶ Cargando datos mock en Supabase + Qdrant Cloud...$(RESET)"
	python3 -m scripts.load_data
	@echo "$(GREEN)✅ Stack cloud listo$(RESET)"

install: ## Instala dependencias Python
	@echo "$(BLUE)▶ Instalando dependencias Python...$(RESET)"
	pip3 install -r requirements.txt --quiet
	@echo "$(GREEN)✅ Dependencias instaladas$(RESET)"

load-data: ## Carga vacantes y usuarios mock al stack
	@echo "$(BLUE)▶ Cargando datos mock...$(RESET)"
	python3 -m scripts.load_data
	@echo "$(GREEN)✅ Datos cargados$(RESET)"

load-jobs: ## Carga solo vacantes
	python3 -m scripts.load_data --only-jobs

load-users: ## Carga solo usuarios
	python3 -m scripts.load_data --only-users

## ── Tests y verificación ──────────────────────────────────────

test: ## Health check completo del stack
	@echo "$(BLUE)▶ Verificando stack...$(RESET)"
	python3 -m scripts.health_check

test-search: ## Prueba búsqueda semántica
	@echo "$(BLUE)▶ Probando búsqueda semántica...$(RESET)"
	python -c "\
from vector_db.embedder import JobEmbedder; \
e = JobEmbedder(); \
r = e.search_jobs('desarrollador python bogotá', top_k=5); \
[print(f'  {i+1}. {j[\"title\"]} @ {j[\"company\"]} — score: {j[\"relevance_score\"]}') for i,j in enumerate(r)]"

test-gateway: ## Prueba el LLM Gateway directamente
	@echo "$(BLUE)▶ Enviando request de prueba al gateway...$(RESET)"
	curl -s -X POST http://localhost:8000/v1/complete \
		-H "Content-Type: application/json" \
		-d '{"agent_id":"test","task_type":"classification","messages":[{"role":"user","content":"Di solo: ok"}],"max_tokens":10}' | python3 -m json.tool

## ── Desarrollo ────────────────────────────────────────────────

gateway-dev: ## Levanta el LLM Gateway en modo desarrollo
	@echo "$(BLUE)▶ Levantando LLM Gateway en :8000$(RESET)"
	@echo "   Docs: http://localhost:8000/docs"
	cd gateway && uvicorn main:app --reload --port 8000

agents-dev: ## Levanta el servidor de agentes en modo desarrollo
	@echo "$(BLUE)▶ Levantando Agents Server en :8001$(RESET)"
	@echo "   Docs: http://localhost:8001/docs"
	uvicorn agents.server:app --reload --port 8001

demo-job-match: ## Demo interactiva del Job Match Agent en terminal
	@echo "$(BLUE)▶ Iniciando demo del Job Match Agent$(RESET)"
	python3 -m agents.job_match.demo

demo-activation: ## Demo interactiva del Early Activation Agent (secuencia 72h)
	@echo "$(BLUE)▶ Iniciando demo del Early Activation Agent$(RESET)"
	python3 -m agents.early_activation.demo

demo-activation-offline: ## Demo del Early Activation Agent sin llamar a Claude
	@echo "$(BLUE)▶ Iniciando demo offline (sin LLM)$(RESET)"
	python3 -m agents.early_activation.demo --no-llm

verify-agent: ## Verifica el Job Match Agent (sin Docker ni APIs externas)
	@echo "$(BLUE)▶ Verificando Job Match Agent...$(RESET)"
	python3 scripts/verify_agent.py

verify-activation: ## Verifica el Early Activation Agent (sin Docker ni APIs externas)
	@echo "$(BLUE)▶ Verificando Early Activation Agent...$(RESET)"
	python3 scripts/verify_early_activation.py

demo-churn: ## Demo del Churn Predictor (detecta usuarios en riesgo)
	@echo "$(BLUE)▶ Iniciando demo del Churn Predictor$(RESET)"
	python3 -m agents.churn_predictor.demo

demo-churn-offline: ## Demo del Churn Predictor sin llamar a Claude
	@echo "$(BLUE)▶ Iniciando demo offline del Churn Predictor$(RESET)"
	python3 -m agents.churn_predictor.demo --no-llm

verify-churn: ## Verifica el Churn Predictor (sin DB ni APIs externas)
	@echo "$(BLUE)▶ Verificando Churn Predictor...$(RESET)"
	python3 scripts/verify_churn_predictor.py

demo-reengagement: ## Demo del Re-engagement Agent (genera mensajes reales)
	@echo "$(BLUE)▶ Iniciando demo del Re-engagement Agent$(RESET)"
	python3 -m agents.reengagement.demo

demo-reengagement-offline: ## Demo del Re-engagement Agent sin llamar a Claude
	@echo "$(BLUE)▶ Iniciando demo offline del Re-engagement Agent$(RESET)"
	python3 -m agents.reengagement.demo --no-llm

verify-reengagement: ## Verifica el Re-engagement Agent (sin DB ni APIs)
	@echo "$(BLUE)▶ Verificando Re-engagement Agent...$(RESET)"
	python3 scripts/verify_reengagement.py

demo-matching: ## Demo del Matching Notifier (alerta candidatos con alto match)
	@echo "$(BLUE)▶ Iniciando demo del Matching Notifier$(RESET)"
	python3 -m agents.matching_notifier.demo

demo-matching-offline: ## Demo del Matching Notifier sin llamar a Claude
	@echo "$(BLUE)▶ Iniciando demo offline del Matching Notifier$(RESET)"
	python3 -m agents.matching_notifier.demo --no-llm

verify-matching: ## Verifica el Matching Notifier (sin DB ni APIs)
	@echo "$(BLUE)▶ Verificando Matching Notifier...$(RESET)"
	python3 scripts/verify_matching_notifier.py

demo-profile: ## Demo del Profile Optimizer (sugerencias de mejora de perfil)
	@echo "$(BLUE)▶ Iniciando demo del Profile Optimizer$(RESET)"
	python3 -m agents.profile_optimizer.demo

demo-profile-offline: ## Demo del Profile Optimizer sin llamar a Claude
	@echo "$(BLUE)▶ Iniciando demo offline del Profile Optimizer$(RESET)"
	python3 -m agents.profile_optimizer.demo --no-llm

verify-profile: ## Verifica el Profile Optimizer (sin DB ni APIs)
	@echo "$(BLUE)▶ Verificando Profile Optimizer...$(RESET)"
	python3 scripts/verify_profile_optimizer.py

demo-employer: ## Demo del Employer Signal Agent (simula + notifica)
	@echo "$(BLUE)▶ Iniciando demo del Employer Signal Agent$(RESET)"
	python3 -m agents.employer_signal.demo

demo-employer-offline: ## Demo del Employer Signal Agent sin llamar a Claude
	@echo "$(BLUE)▶ Iniciando demo offline del Employer Signal Agent$(RESET)"
	python3 -m agents.employer_signal.demo --no-llm

verify-employer: ## Verifica el Employer Signal Agent (sin DB ni APIs)
	@echo "$(BLUE)▶ Verificando Employer Signal Agent...$(RESET)"
	python3 scripts/verify_employer_signal.py

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-18s$(RESET) %s\n", $$1, $$2}'

.DEFAULT_GOAL := help
