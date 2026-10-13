# Infrastructure

[main.bicep](main.bicep) deploys the lab landing zone into one resource group: Foundry (account, project,
gpt-5-mini and gpt-5-nano deployments), API Management (Basic v2) as the gateway in front of the agents, Log
Analytics and Application Insights. [agents-api-policy.xml](agents-api-policy.xml) is the gateway policy.

Foundry agents aren't Azure resources, so Bicep can't create them. [scripts/create_agents.py](../scripts/create_agents.py)
creates them from [src/agents](../src/agents) after the deployment.

## Deploy

The Skillable lifecycle action runs this for each learner. To test in your own subscription:

```powershell
az group create --name rg-prel35-lab --location eastus2
pip install -r requirements.txt
.\scripts\deploy.ps1 -ResourceGroup rg-prel35-lab -LearnerPrincipalId <learner-object-id>
```

The identity running it needs Owner or Role Based Access Control Administrator on the resource group, because the
template assigns **Foundry User** to API Management's managed identity, the learner and itself. The learner only
needs Contributor. The deployment takes about 15 to 20 minutes, mostly API Management.
