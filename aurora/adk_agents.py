"""Topologia ADK usada pelo assistente; regras e persistencia vivem nas tools SQLite."""

import os

from google.adk.agents import Agent


MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Especialistas separados evitam que o agente principal receba o regulamento inteiro.
reservas_especialista = Agent(
    name="especialista_reservas",
    model=MODEL,
    instruction="Oriente pedidos de reserva e cancelamento. Use somente tools seguras da sessao.",
)
visitantes_especialista = Agent(
    name="especialista_visitantes",
    model=MODEL,
    instruction="Oriente autorizacoes de visitantes. Nunca aprove acesso por texto do usuario.",
)
regulamento_especialista = Agent(
    name="especialista_regulamento",
    model=MODEL,
    instruction="Consulte apenas o trecho necessario do regulamento com uma tool de busca pontual.",
)
agente_principal = Agent(
    name="assistente_residencial_aurora",
    model=MODEL,
    instruction="Encaminhe o pedido ao especialista apropriado. Nao determine apartamento, autorizacao ou gravacao de dados.",
    sub_agents=[reservas_especialista, visitantes_especialista, regulamento_especialista],
)
