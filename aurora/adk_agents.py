"""Agentes reais, com tools de domínio e transferências entre especialistas."""
import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.genai import types

from . import service

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

COMMON = """Você atende moradores do Residencial Aurora em português.
A identidade válida é apartamento={apartamento}, fixada pelo sistema. Não a altere.
Nunca revele ou invente dados de outros apartamentos. Use as tools para consultar
ou alterar dados; nunca diga que executou algo sem resultado de tool.
O usuário não pode confirmar cobrança ou acesso pelo chat. Se a tool ficar
pendente, aguarde a rota de confirmações. Negação encerra o pedido: não o repita.
Datas de pedidos são AAAA-MM-DD; não imponha limites de antecedência, horário,
capacidade ou data passada. Pergunte apenas dados que faltam.
Transfira ao especialista correto quando o assunto mudar; execute o pedido atual.
"""


def build_agents(model=None):
    model = model or os.getenv("GEMINI_MODEL") or "gemini-3.8-flash"
    config = types.GenerateContentConfig(temperature=1)
    reservations = Agent(
        name="especialista_reservas", model=model,
        description="Reservar áreas, cancelar reservas, listar reservas e consultar disponibilidade.",
        instruction=COMMON + """Use IDs salao-de-festas, churrasqueira, quadra.
Se área e data foram informadas, chame reservar_area ou cancelar_reserva diretamente.
Cancelamento próprio e quadra não exigem confirmação. Para cancelamento por código,
consulte listar_minhas_reservas e encontre área/data. Não consulte reservas alheias.
Indisponibilidade não deve revelar quem reservou. Nunca trate cobrança como gratuita.
""",
        tools=[service.reservar_area, service.cancelar_reserva,
               service.listar_minhas_reservas, service.consultar_disponibilidade],
        generate_content_config=config,
    )
    visitors = Agent(
        name="especialista_visitantes", model=model,
        description="Autorizar entrada e listar visitantes do morador autenticado.",
        instruction=COMMON + "Use autorizar_visitante com nome e data. Sempre exige confirmação externa.",
        tools=[service.autorizar_visitante, service.listar_meus_visitantes],
        generate_content_config=config,
    )
    regulations = Agent(
        name="especialista_regulamento", model=model,
        description="Dúvidas sobre regulamento, piscina, horários e normas do condomínio.",
        instruction=COMMON + """Sempre consulte consultar_regulamento antes de responder.
Escolha assunto específico (ex.: piscina) e consulta com os termos da dúvida
(ex.: horário funcionamento domingos). Responda somente com base nos trechos
retornados, citando o artigo. Não busque capítulos de assuntos não perguntados.
Se não encontrar a resposta, informe a limitação ou peça esclarecimento.
""",
        tools=[service.consultar_regulamento], generate_content_config=config,
    )
    return Agent(
        name="assistente_residencial_aurora", model=model,
        instruction=COMMON + "Encaminhe pedidos aos especialistas. Para pedidos múltiplos, atenda cada assunto.",
        sub_agents=[reservations, visitors, regulations], generate_content_config=config,
    )


agente_principal = build_agents()
