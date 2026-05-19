"""
Custom Agent Name Example

Demonstrates how to run multiple Langclaw instances with different agent names
for multi-tenant deployments.

Setup:
    export LANGCLAW__AGENT_NAME=tenant_a
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_a
    python examples/custom_agent_name.py

To run multiple instances:
    # Terminal 1
    export LANGCLAW__AGENT_NAME=tenant_a
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_a
    python examples/custom_agent_name.py

    # Terminal 2
    export LANGCLAW__AGENT_NAME=tenant_b
    export LANGCLAW__CONFIG_DIR=~/.langclaw_tenant_b
    python examples/custom_agent_name.py
"""

from langclaw import Langclaw
from langclaw.config.schema import load_config


def main():
    config = load_config()

    # Show the configured agent name and paths
    print(f"Starting agent: {config.agent_name}")
    print(f"Config directory: {config.config_dir}")
    print(f"RabbitMQ queue: {config.bus.rabbitmq.queue_name}")
    print(f"Kafka topic: {config.bus.kafka.topic}")
    print()

    app = Langclaw()

    @app.tool()
    def greet(name: str) -> dict:
        """Greet the user by name."""
        return {"message": f"Hello {name} from {config.agent_name}!"}

    @app.tool()
    def whoami() -> dict:
        """Show agent identity."""
        return {
            "agent_name": config.agent_name,
            "config_dir": str(config.config_dir),
            "queue_name": config.bus.rabbitmq.queue_name,
        }

    print(f"Agent '{config.agent_name}' is ready!")
    print("Try the /agent command or send a message to test.")
    print()

    app.run()


if __name__ == "__main__":
    main()
