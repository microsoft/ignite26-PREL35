// Lab 0 landing zone: Foundry (Entra ID only) behind an API Management AI gateway, with monitoring.
// Deployed per learner resource group by the Skillable lifecycle action (scripts/deploy.ps1).
// The role assignments need Owner or Role Based Access Control Administrator, which the learner (Contributor) lacks.
targetScope = 'resourceGroup'

param location string = resourceGroup().location

@description('Object ID of the learner. Gets Foundry User so they can see and evaluate the agents. Empty to skip.')
param learnerPrincipalId string = ''

@description('Object ID of the identity that runs scripts/create_agents.py. Empty to skip.')
param operatorPrincipalId string = ''

@description('Agents published through the gateway. triage-lite is triage on a smaller model (Lab 4).')
param agentNames array = ['triage', 'reviewer', 'triage-lite']

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

// Lab 3: the models the bank has approved. Any other model deployment in this resource group is denied.
resource approvedModels 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: 'approved-ai-models'
  properties: {
    displayName: 'Only approved AI models can be deployed'
    description: 'Lab 3: the bank approves gpt-5-mini and gpt-5-nano. Deployments of any other model are denied.'
    // Built-in: Foundry model deployments should only use approved models
    policyDefinitionId: tenantResourceId('Microsoft.Authorization/policyDefinitions', 'aafe3651-cb78-4f68-9f81-e7e41509110f')
    parameters: {
      effect: { value: 'Deny' }
      allowedAssetIds: { value: [for m in models: 'azureml://registries/azure-openai/models/${m.name}/'] }
    }
  }
  dependsOn: [deployments] // assigned after the approved deployments exist
}

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

var namedValues = [
  { name: 'tenant-id', value: tenant().tenantId }
  { name: 'content-safety-endpoint', value: 'https://${foundry.name}.cognitiveservices.azure.com/' }
  { name: 'prompt-shields', value: 'off' } // Lab 2: learners switch this to "on"
  { name: 'caller-identity', value: 'gateway' } // Lab 2: learners switch this to "obo"
  { name: 'token-limit', value: '200000' } // Lab 4: tokens per minute per caller
  { name: 'model-routing', value: 'off' } // Lab 4: learners switch this to "on"
  { name: 'routing-max-score', value: '65' } // Lab 4: alerts scored up to this go to triage-lite
  // Lab 2: scripts/setup_obo.ps1 replaces these with the app registration it creates.
  { name: 'obo-client-id', value: '00000000-0000-0000-0000-000000000000' }
  { name: 'obo-client-secret', value: 'not-set' }
]

@batchSize(1)
resource apimNamedValues 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for nv in namedValues: {
  parent: apim
  name: nv.name
  properties: { displayName: nv.name, value: nv.value }
}]

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
  dependsOn: [apimNamedValues, projectBackend]
}

// Lab 4: the gateway sends request logs and token metrics (llm-emit-token-metric) to Application Insights.
resource apimLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsights.id
    credentials: { connectionString: appInsights.properties.ConnectionString }
  }
}

resource agentsApiDiagnostics 'Microsoft.ApiManagement/service/apis/diagnostics@2024-05-01' = {
  parent: agentsApi
  name: 'applicationinsights'
  properties: {
    loggerId: apimLogger.id
    alwaysLog: 'allErrors'
    metrics: true // required for custom metrics such as the token metrics
    sampling: { samplingType: 'fixed', percentage: 100 }
    verbosity: 'information'
  }
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

// No principalType: Azure looks it up, so a user or a service principal can be the operator.
var foundryUsers = filter(union([learnerPrincipalId], [operatorPrincipalId]), id => !empty(id))

resource foundryUserAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in foundryUsers: {
  scope: foundry
  name: guid(foundry.id, id, foundryUserRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', foundryUserRole)
    principalId: id
  }
}]

output FOUNDRY_PROJECT_ENDPOINT string = 'https://${foundry.name}.services.ai.azure.com/api/projects/${project.name}'
output MODEL_DEPLOYMENT string = deployments[0].name
output APIM_GATEWAY_URL string = apim.properties.gatewayUrl
output APIM_NAME string = apim.name
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appInsights.properties.ConnectionString
