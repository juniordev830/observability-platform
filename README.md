# Observability Platform

Serviço local com métricas Prometheus (contador HTTP e tempo ativo). Endpoints: `GET /health` e `GET /metrics`, sem bibliotecas externas.

O endpoint `GET /ready` informa que este processo demonstrativo está pronto para receber requisições. As rotas também aceitam `HEAD`, que retorna os cabeçalhos sem o corpo da resposta. O contador de métricas inclui as chamadas de `GET` e `HEAD` a `/ready`.

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

## Verificação antes de contribuir

O [guia de validação](docs/GUIA_DE_VALIDACAO.md) descreve o estado do código, as verificações reproduzíveis e os riscos a observar ao revisar novas alterações.
