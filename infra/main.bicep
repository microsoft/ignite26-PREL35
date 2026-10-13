// Lab 0 landing zone: Foundry (Entra ID only) behind an API Management AI gateway, with monitoring.
// Deployed per learner resource group by the Skillable lifecycle action (scripts/deploy.ps1).
// The role assignments need Owner or Role Based Access Control Administrator, which the learner (Contributor) lacks.
targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Object ID of the learner. Gets Foundry User so they can see and evaluate the agents. Empty to skip.')
param learnerPrincipalId string = ''

@description('Object ID of the identity that runs scripts/create_agents.py. Empty to skip.')
param operatorPrincipalId string = ''
@allowed(['User', 'ServicePrincipal'])
param operatorPrincipalType string = 'ServicePrincipal'

@description('Agents published through the gateway. Each must exist in src/agents/<name>.md.')
param agentNames array = ['triage', 'reviewer']

@description('Model deployments (capacity in 1K tokens per minute). The agents start on the first one.')
param models array = [
  { name: 'gpt-5-mini', version: '2025-08-07', capacity: 60 }
  { name: 'gpt-5-nano', version: '2025-08-07', capacity: 30 } // Lab 1: the replacement in the model swap
]

var token = toLower(uniqueString(resourceGroup().id))
var tags = { workload: 'fincrime-triage' }
var foundryUserRole = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

// ---------------------------------------------------------------- monitoring
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

// ---------------------------------------------------------------- Foundry
resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: 'aif-${token}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'aif-${token}'
    allowProjectManagement: true
    disableLocalAuth: true // Entra ID only, no API keys
    publicNetworkAccess: 'Enabled'
    // Explicit, even though it's the default: some tenant policies can't evaluate an account without ipRules.
    networkAcls: { defaultAction: 'Allow', ipRules: [], virtualNetworkRules: [] }
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: foundry
  name: 'fincrime'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'Financial crime triage'
    description: 'Triage and reviewer agents for transaction monitoring alerts'
  }
}

// Sends agent traces to Application Insights.
resource appInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'appinsights'
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: true
    credentials: { key: appInsights.properties.ConnectionString }
    metadata: { ApiType: 'Azure', ResourceId: appInsights.id }
  }
}

// The account accepts one change at a time, so deployments wait for the project and its connection, and run one by one.
@batchSize(1)
resource deployments 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [for m in models: {
  parent: foundry
  name: m.name
  sku: { name: 'GlobalStandard', capacity: m.capacity }
  properties: {
    model: { format: 'OpenAI', name: m.name, version: m.version }
    raiPolicyName: 'Microsoft.DefaultV2'
  }
  dependsOn: [project, appInsightsConnection]
}]

// ---------------------------------------------------------------- API Management: the only way in for apps
resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-${token}'
  location: location
  tags: tags
  sku: { name: 'BasicV2', capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: 'lab-admin@contoso.example'
    publisherName: 'FinCrime lab'
  }
}

resource tenantIdValue 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'tenant-id'
  properties: { displayName: 'tenant-id', value: tenant().tenantId }
}

resource projectBackend 'Microsoft.ApiManagement/service/backends@2024-05-01' = {
  parent: apim
  name: 'foundry-project'
  properties: {
    protocol: 'http'
    url: 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
  }
}

resource agentsApi 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'agents'
  properties: {
    displayName: 'Published agents'
    path: 'agents'
    protocols: ['https']
    subscriptionRequired: false // callers authenticate with Entra ID tokens instead
  }
}

// One operation per published agent: an agent that isn't listed here can't be reached through the gateway.
resource agentOperations 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for name in agentNames: {
  parent: agentsApi
  name: name
  properties: {
    displayName: '${name} agent'
    method: 'POST'
    urlTemplate: '/${name}/responses'
  }
}]

resource agentsApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: agentsApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('agents-api-policy.xml')
  }
  dependsOn: [tenantIdValue, projectBackend]
}

// ---------------------------------------------------------------- roles
resource apimFoundryUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, apim.id, foundryUserRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRole)
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

var foundryUsers = concat(
  empty(learnerPrincipalId) || learnerPrincipalId == operatorPrincipalId ? [] : [{ id: learnerPrincipalId, type: 'User' }],
  empty(operatorPrincipalId) ? [] : [{ id: operatorPrincipalId, type: operatorPrincipalType }]
)

resource foundryUserAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for p in foundryUsers: {
  scope: foundry
  name: guid(foundry.id, p.id, foundryUserRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRole)
    principalId: p.id
    principalType: p.type
  }
}]

output FOUNDRY_PROJECT_ENDPOINT string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output MODEL_DEPLOYMENT string = deployments[0].name
output APIM_GATEWAY_URL string = apim.properties.gatewayUrl
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
