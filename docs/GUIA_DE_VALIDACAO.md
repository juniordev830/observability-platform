# Operação e qualidade — Observability Platform

## Escopo existente

Servidor Python local com endpoints de saúde, prontidão e métricas Prometheus da própria aplicação.

## Código e testes

`server.py`, `tests/test_server.py`, `tests/test_readiness.py`.

## Reprodução local

`python -m unittest discover -s tests -v`; `python server.py`; acessar `/health`, `/ready` e `/metrics`.

Ao alterar funcionalidade, registrar no PR os comandos executados e os resultados; não tratar verificações não executadas como aprovadas.

## Cenários a incluir na regressão

Testar GET/HEAD em prontidão, contagem de requisições, cliente que desconecta e concorrência em leitura de métricas.

## Riscos e evolução

Não expor métricas internas sem autenticação e segmentação de rede em uso real; identificar claramente que o serviço é demonstrativo.

### Checklist de entrega

- [ ] Novo comportamento ligado a caso de uso real.
- [ ] Testes e exemplo documentados, quando aplicável.
- [ ] Nenhum segredo ou dado de cliente adicionado.
- [ ] Código revisado em branch, sem merge automático.
