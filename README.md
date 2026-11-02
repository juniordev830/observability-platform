# Observability Platform

Serviço local com métricas Prometheus (contador HTTP e tempo ativo). Endpoints: `GET /health` e `GET /metrics`, sem bibliotecas externas.

## Estado

**MVP funcional / projeto inicial de portfólio.** Implementa o núcleo descrito abaixo, mas não é produto pronto para produção.

## Como executar

```bash
python server.py
```

## Testes

```bash
python -m unittest discover -s tests -v
```

O GitHub Actions executa os testes automaticamente em pushes e pull requests.

## Segurança e limitações

Apenas demonstração local; não contém autenticação, logs estruturados, tracing ou alertas. Não publique a porta na internet sem controle de acesso.

## Evolução planejada

Adicionar Docker Compose com Prometheus e Grafana, OpenTelemetry e alertas.

---
Projeto de Engenharia de Software mantido na conta [juniordev830](https://github.com/juniordev830).
