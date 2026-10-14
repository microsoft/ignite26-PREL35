# Infrastructure

[main.bicep](main.bicep) deploys the lab landing zone into one resource group: Foundry (account, project, a
gpt-5-mini deployment the agents use, and a gpt-4.1-mini deployment for the Lab 1 model retirement drill), API
Management (Basic v2) as the gateway in front of the agents, Log Analytics and Application Insights.
[agents-api-policy.xml](agents-api-policy.xml) is the gateway policy.

Foundry agents aren't Azure resources, so Bicep can't create them. [scripts/create_agents.ps1](../scripts/create_agents.ps1)
creates them from [scripts/agents](../scripts/agents) after the deployment.

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

Resource deployment and agent publication do not complete Lab 0's gateway authentication. Before attendees start,
an authorized deployment owner must also provision the dedicated API and confidential Lab client registrations,
assign `Agents.Invoke`, and configure the gateway:

```powershell
.\scripts\setup-entra-apps.ps1 -ResourceGroup rg-prel35-lab
```

Follow the [Lab 0 owner setup](../instructions/lab-0-setup/README.md#deployment-owner-configure-microsoft-entra-authentication-for-the-gateway)
for tenant permissions, isolated registration names, learner VM provisioning, and secret handling. Run the setup
on each learner VM so its ignored `.env` has the client credential; a setup on another machine does not populate it.
Sign out the owner before handing the VM to an attendee.

Rerun the owner setup after Bicep redeployment, which resets the gateway audience to its bootstrap value.
Do not run this tenant provisioning automatically as a learner. Lab 1's pinned-version evaluator instead uses the
learner's interactive Azure identity and the direct Foundry path.
