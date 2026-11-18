# Resumo de uma coleta de /metrics. Usar LC_ALL=C para saída numérica estável.
/^demo_responses_total[{]/ {
    if (NF != 2 || $1 !~ /^demo_responses_total\{status="[1-5][0-9][0-9]"\}$/ || $2 !~ /^[0-9]+$/) {
        erro = "amostra de respostas inválida"
        exit 2
    }
    codigo = $1
    sub(/^.*="/, "", codigo)
    sub(/"\}$/, "", codigo)
    if (codigo in vistos) {
        erro = "código repetido; informe uma única coleta"
        exit 2
    }
    vistos[codigo] = 1
    total += $2
    classe = substr(codigo, 1, 1)
    por_classe[classe] += $2
    amostras++
}
END {
    if (!amostras && !erro) erro = "coleta sem demo_responses_total"
    if (erro) {
        print erro > "/dev/stderr"
        exit 2
    }
    print "classe\trespostas\tpercentual"
    for (classe = 1; classe <= 5; classe++) {
        quantidade = por_classe[classe] + 0
        percentual = total ? 100 * quantidade / total : 0
        printf "%dxx\t%.0f\t%.2f\n", classe, quantidade, percentual
    }
}
