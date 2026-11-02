# Temporary lab - hosted evidence agent

## What this exercise covers

Configure public-web research and deploy the standalone **evidence-agent** into your existing Foundry project.
This temporary setup walkthrough is separate from Labs 0 to 4. It does not connect the evidence agent to the
triage/reviewer workflow or run an investigation.

Unlike the Markdown prompt agents published earlier, this hosted agent runs Python code in Foundry.
Its research tool uses the Search knowledge base created during Lab 0.

## Prerequisites

- Complete the setup step in [Lab 0](../lab-0-setup/README.md): the repository has a populated `.env`,
  a seeded Search index, and the `fincrime-research` knowledge base.
- Keep Azure CLI signed in to the lab account and subscription.
- Use a terminal in the repository root, with Azure Developer CLI (`azd`) and the extensions required by
  [azure.yaml](../../azure.yaml) available.

The lab's infrastructure and scoped permissions are already provisioned. You do not need permission to assign roles.

## 1. Understand Web IQ

**Web IQ** supplies public-web context through an MCP service. The Search knowledge base combines that context
with the synthetic typology notes already in the index. Public context is not evidence about a customer.

Web IQ requires an API key from a profile linked to the lab's existing Web IQ billing resource.
The preparation command guides you through the profile portal and hidden key input; you do not need to
configure the Foundry and Search integrations by hand.

**Cost and data boundary:** first-time preparation runs billable Web IQ and knowledge-base checks using a fixed,
public DOJ topic. Do not send customer data, identifiers, or full case prompts to public-web research.

## 2. Configure Web IQ and the deployment environment

Run the combined preparation command:

```powershell
uv run configure-evidence-agent
```

The command reads the resource group and deployment settings from `.env`; do not enter the resource group again.
It configures the azd environment and disables automatic role assignments, then sets up Web IQ if needed.

If prompted for a key:

1. In the Web IQ profile portal opened by the command, select the profile linked to the billing resource ID
   shown in the terminal.
2. Create an API key with the **web** service enabled.
3. Paste it into the hidden terminal prompt, not into chat or the lab instructions.

Invalid or rejected keys trigger another prompt; press Ctrl+C to cancel. The key is saved in the ignored `.env`
and configured separately in the Foundry connection and Search knowledge source. Treat it as a shared lab credential.
If both integrations are already configured, the command reuses them without asking for a key or running another search.

Continue only when the command reports that Web IQ and the evidence-agent environment are ready.
If `.env` is missing or incomplete, return to Lab 0 setup. For an access error or a target mismatch, stop and ask
the proctor; do not grant roles or retarget another lab.

## 3. Deploy the hosted agent

From the repository root, run:

```powershell
azd deploy
```

Deploy all services, not just `azd deploy evidence-agent`. The configuration creates or updates the
**evidence-research** connection, **evidence-tools** Toolbox, and hosted **evidence-agent**, in dependency order.
Foundry builds the Python dependencies remotely; you do not need a local container build.

This command does not provision infrastructure, assign roles, republish the prompt agents, or change gateway controls.
There is no predeploy setup hook: finish the preparation command before deploying.

## 4. Confirm deployment

Wait for `azd deploy` to report success. Open the [Foundry portal](https://ai.azure.com), select the existing
`fincrime` project, and confirm that **evidence-agent** appears as a hosted agent.

Stop here for this temporary exercise. Configuration and deployment are complete; invoking the agent and connecting
it to the investigation workflow are outside this walkthrough.
