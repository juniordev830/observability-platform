# Observability Platform

Serviço de observabilidade **local** em Python 3.12, sem dependências externas.
A biblioteca mantém compatibilidade com os endpoints originais e adiciona
métricas Prometheus, janelas deslizantes, SLOs, alertas, incidentes e diagnósticos JSON.

> **Escopo:** demonstração de engenharia de software e testes. Não é um produto
> de monitoramento distribuído nem um substituto para Prometheus/Grafana.

## Executar

Requer Python 3.10 ou superior (CI: 3.12).

~~~bash
python server.py
~~~

O servidor escuta **somente em 127.0.0.1:9091** por padrão.
Para testes automatizados, a porta 0 escolhe uma porta local livre.

## Endpoints

| Rota | Resposta | Observações |
| --- | --- | --- |
| GET/HEAD /health | 200, status ok | Liveness independente das dependências |
| GET/HEAD /ready | 200 ou 503 | Readiness com checks obrigatórios |
| GET/HEAD /metrics | 200, Prometheus text | Contadores originais + métricas HTTP |
| GET/HEAD /health/details | 200 ou 503, JSON | Detalhes de checks e duração |
| GET/HEAD /summary | 200, JSON | SLO, alertas, incidentes e métricas |
| GET/HEAD /alerts | 200, JSON | Estado atual das regras de alerta |
| GET/HEAD /incidents | 200, JSON | Incidentes locais |
| GET/HEAD /events | 200, JSON | Eventos auditáveis com paginação |

A rota /events aceita category, after (cursor de sequência) e limit (1 a 500).
Parâmetros inválidos retornam 400. Rotas desconhecidas retornam 404.
As respostas têm Cache-Control: no-store e X-Content-Type-Options: nosniff.
O método HEAD devolve os mesmos cabeçalhos, sem corpo.

## Biblioteca de métricas

~~~python
from telemetry import MetricRegistry

registry = MetricRegistry(max_series=100)
registry.register_counter("orders_total", "Pedidos processados", ("result",))
registry.register_gauge("workers_active", "Trabalhadores ativos")
registry.register_histogram("processing_seconds", "Tempo de processamento",
                            buckets=(0.1, 0.5, 1, 5))
registry.increment("orders_total", labels={"result": "success"})
registry.set_gauge("workers_active", 2)
registry.observe("processing_seconds", 0.35)
print(registry.render())
print(registry.snapshot())
~~~

O registro exige nomes Prometheus válidos, conjuntos de labels exatos e valores
finitos. Séries têm limite configurável para impedir crescimento ilimitado.
Histogramas geram buckets cumulativos, soma e contagem. Operações são protegidas
por RLock; snapshots são cópias independentes.

## Janelas temporais e eventos

~~~python
from telemetry import EventLog, RollingWindow

latency = RollingWindow(seconds=60, capacity=1000)
latency.add(0.05)
latency.add(0.2)
print(latency.summary())  # média, p50, p95, taxa, total

events = EventLog(capacity=500)
events.append("deploy", "Serviço iniciado", {"environment": "local"})
print(events.list(category="deploy", after=0, limit=20))
~~~

A janela descarta amostras antigas; o log preserva somente os eventos mais
recentes, com IDs sequenciais para paginação. Tudo fica **em memória**.

## Health checks e SLO

~~~python
from monitoring import HealthRegistry, SLOTracker

health = HealthRegistry()
health.register("database", lambda: (True, "conexão local ok"))
health.register("cache", lambda: False, required=False)
print(health.evaluate().to_dict())

slo = SLOTracker(target=0.99, window_seconds=3600)
slo.record(True)
slo.record(False)
print(slo.snapshot().to_dict())
~~~

Um check opcional com falha não derruba o readiness. O relatório não expõe
mensagens de exceção internas, apenas o tipo da exceção. O SLO usa amostras
de sucesso/falha e calcula razão observada, orçamento de erros e burn rate.
Sem tráfego, conformidade é desconhecida (null no JSON).

## Alertas e incidentes

~~~python
from monitoring import AlertManager, AlertRule, IncidentStore

alerts = AlertManager()
alerts.register(AlertRule("latency_high", threshold=0.5,
                          operator=">", for_seconds=10, severity="warning"))
alerts.evaluate("latency_high", 0.8)  # pending até 10 s de violação contínua
print(alerts.status())

incidents = IncidentStore()
incident = incidents.create("API lenta", "warning")
incidents.acknowledge(incident["id"])
incidents.add_note(incident["id"], "Análise em andamento")
incidents.resolve(incident["id"], "Serviço normalizado")
print(incidents.summary())
~~~

As regras suportam operadores de comparação, estados inactive, pending e firing,
silenciamentos temporários e histórico de transições. Incidentes suportam criação,
reconhecimento, notas e resolução, com validação de transições.

## Integração com o servidor

~~~python
from monitoring import HealthRegistry, AlertRule
from server import new_server

checks = HealthRegistry()
checks.register("dependency", lambda: True)
server = new_server(port=0, health_checks=checks)
server.observability["events"].append("deploy", "Inicializado")
server.observability["alerts"].register(AlertRule("high_errors", 5))
# Em aplicações reais, gerencie serve_forever(), shutdown() e server_close().
server.server_close()
~~~

O servidor expõe somente **leituras** HTTP. Mutação de alertas, eventos e
incidentes ocorre pela API Python local. Não há autenticação nem persistência.

## Testes

~~~bash
python -m compileall -q .
python -m unittest discover -s tests -v
~~~

Os testes incluem compatibilidade de /health, /ready, /metrics, HEAD,
erros HTTP, contadores concorrentes, labels escapadas, cardinalidade, histogramas,
percentis, retenção de eventos, falhas de dependências, SLOs, silenciamento,
máquina de estados de alertas e ciclo de vida de incidentes.

## Segurança, limites e evolução

- **Não exponha a porta na internet** sem autenticação, TLS, limitação de taxa e
  proteção de rede. A configuração padrão é loopback.
- Não armazene segredos ou dados pessoais em labels, mensagens ou incidentes.
- Os armazenamentos são locais, limitados e voláteis; reiniciar perde o estado.
- Health checks são síncronos; callbacks lentos podem atrasar a resposta.
- Não há agregação entre processos, persistência, tracing distribuído nem
  garantias de entrega de alertas. Futuras integrações podem incluir
  OpenTelemetry, Prometheus, Grafana e um armazenamento durável.

Projeto mantido em https://github.com/juniordev830.
