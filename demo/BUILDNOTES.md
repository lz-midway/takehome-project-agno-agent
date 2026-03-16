# Build Notes

- **Primary AI tool**: Claude (claude.ai web interface) was used throughout for code generation, debugging, and documentation.

- **Greenfield speed-up**: Project structure, Agno Workflow boilerplate, AgentOS wiring, Pydantic state schema, agent system prompts, and test scaffolding were all generated from a written spec — getting from zero to a working skeleton was significantly faster than writing by hand.

- **Documentation**: README architecture diagram, agent descriptions, and tradeoffs section were drafted with Claude and edited for accuracy.

- **Failure on Agno API research**: Claude got the `RunResponse` import path wrong across four attempts before `grep` confirmed the class doesn't exist in agno 2.5.9. Similarly, `DuckDuckGoTools(news=True, search=False)` and `workflow_id=` as a constructor keyword were found to be incorrect and changed.

- **Initial pipeline design was done manually**: The four-agent sequential structure and typed handoff boundaries are designed before being fed to Claude.

- **Typed Pydantic handoffs changes made personally later**: To match the "no stringly-typed handoffs" requirement, the code is refactored to explicit typed input models (`PlannerInput`, `BrowserInput`, etc.) and a `WorkflowEvent` discriminated union. Refactoring is aided by Claude.

- **`NoneType` agent bug required manual diagnosis**: `model_post_init` not firing in agno 2.5.9 left all four agents as `None` at runtime. Identifying that the Agno base class was silently skipping the hook required some manual intervention.

- **AgentOS UI remains unresolved**: Time was spent debugging `ChatConfig` quick prompt limits and constructor mismatches. The `Workflow.run()` override signature failed to function after repeated debugging. The CLI is the working interface.