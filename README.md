# Observability Platform

Serviço demonstrativo local em **Python**, sem dependências externas, com métricas compatíveis com o formato de exposição do Prometheus.

## Endpoints

- `GET /health` e `GET /ready` respondem JSON e status 200.
- `GET /metrics` expõe as métricas em texto.
- Rotas desconhecidas retornam 404.
- Todos os caminhos aceitam `HEAD` com os mesmos cabeçalhos de GET, mas sem corpo.

## Métricas

- `demo_requests_total{path="/health|/ready|/metrics|/other"}`: contagem por **quatro rótulos fixos**, evitando cardinalidade alta de URLs ou parâmetros de consulta.
- `demo_responses_total{status="200|404"}`: contagem de respostas HTTP por status. `GET` e `HEAD` são contabilizados, incluindo a própria consulta a `/metrics`.
- `demo_uptime_seconds`: tempo desde o início do processo.

O registro dos dois contadores é protegido pelo mesmo lock, mantendo consistência entre eles durante chamadas simultâneas. Não são armazenados IPs, tokens nem caminhos arbitrários.

## Executar

```bash
python server.py
# Em outro terminal:
curl http://127.0.0.1:9091/metrics
```

Por padrão o processo escuta apenas em `127.0.0.1:9091`.

## Testar

```bash
python -m unittest discover -s tests -v
```

O GitHub Actions testa o servidor HTTP real, `HEAD`, readiness, resposta 404, contagem de status, concorrência e invariantes dos contadores.

## Segurança e limites

Demo de processo único: sem autenticação, Prometheus/Grafana integrados, logging estruturado, tracing, persistência, alertas ou scraping remoto. **Não exponha a porta na internet** sem controle de acesso. A leitura de `/metrics` contabiliza o próprio scrape, como outras requisições.

Licença ainda não definida.
