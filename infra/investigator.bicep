targetScope = 'resourceGroup'

@description('Search region; can differ from Foundry if the lab region has no Search capacity.')
param searchLocation string = 'eastus'

param operatorPrincipalId string = ''
param learnerPrincipalId string = ''
param investigatorPrincipalId string = ''
param investigatorIndexName string = 'fincrime-schemes'
param investigatorKnowledgeBaseName string = 'fincrime-research'

@description('Existing Foundry account containing the investigator project.')
param foundryAccountName string = 'aif-${toLower(uniqueString(resourceGroup().id))}'

param foundryProjectName string = 'fincrime'

var token = toLower(uniqueString(resourceGroup().id))
var tags = { workload: 'fincrime-triage' }
var contributorRole = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
var searchServiceContributorRole = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
var searchIndexDataContributorRole = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var searchIndexDataReaderRole = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var openAiUserRole = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var embeddingModel = 'text-embedding-3-small'

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundry
  name: foundryProjectName
}

// The learner creates and seeds the index after provisioning using scripts/setup_lab.ps1.
resource investigatorSearch 'Microsoft.Search/searchServices@2025-05-01' = {
  name: 'search-${token}'
  location: searchLocation
  tags: tags
  sku: { name: 'basic' }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    semanticSearch: 'free'
  }
}

resource searchEmbeddingUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, investigatorSearch.id, openAiUserRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', openAiUserRole)
    principalId: investigatorSearch.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Web IQ API keys are created separately in its portal, never emitted as deployment outputs.
resource webIq 'Microsoft.WebIQ/accounts@2026-06-01-preview' = {
  name: 'webiq-${token}'
  location: 'global'
  tags: tags
  kind: 'WebIQ.Grounding'
  sku: { name: 'G3' }
  properties: {}
}

resource learnerWebIqContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(learnerPrincipalId)) {
  scope: webIq
  name: guid(webIq.id, learnerPrincipalId, contributorRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRole)
    principalId: learnerPrincipalId
  }
}

var searchSetupPrincipals = filter(union([operatorPrincipalId], [learnerPrincipalId]), id => !empty(id))

resource searchServiceContributors 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in searchSetupPrincipals: {
  scope: investigatorSearch
  name: guid(investigatorSearch.id, id, searchServiceContributorRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRole)
    principalId: id
  }
}]

resource searchIndexDataContributors 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for id in searchSetupPrincipals: {
  scope: investigatorSearch
  name: guid(investigatorSearch.id, id, searchIndexDataContributorRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRole)
    principalId: id
  }
}]

// The hosted agent has a different identity from the project and gateway.
resource investigatorSearchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(investigatorPrincipalId)) {
  scope: investigatorSearch
  name: guid(investigatorSearch.id, investigatorPrincipalId, searchIndexDataReaderRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRole)
    principalId: investigatorPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource projectSearchReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: investigatorSearch
  name: guid(investigatorSearch.id, project.id, searchIndexDataReaderRole)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRole)
    principalId: project.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource investigatorSearchConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'investigator-search'
  properties: {
    category: 'CognitiveSearch'
    target: 'https://${investigatorSearch.name}.search.windows.net'
    // Foundry supports this identity type, but the ARM type catalog does not list it.
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ApiVersion: '2025-09-01'
      ResourceId: investigatorSearch.id
      type: 'azure_ai_search'
    }
  }
  dependsOn: [projectSearchReader]
}

output AZURE_SEARCH_SERVICE_NAME string = investigatorSearch.name
output AZURE_SEARCH_ENDPOINT string = 'https://${investigatorSearch.name}.search.windows.net'
output AZURE_SEARCH_LOCATION string = searchLocation
output AZURE_SEARCH_INDEX_NAME string = investigatorIndexName
output AZURE_SEARCH_CONNECTION_NAME string = investigatorSearchConnection.name
output AZURE_SEARCH_CONNECTION_ID string = investigatorSearchConnection.id
output AZURE_SEARCH_KNOWLEDGE_BASE_NAME string = investigatorKnowledgeBaseName
output INVESTIGATOR_PLANNING_DEPLOYMENT string = 'gpt-5-mini'
output INVESTIGATOR_EMBEDDING_ENDPOINT string = 'https://${foundry.name}.openai.azure.com'
output INVESTIGATOR_EMBEDDING_DEPLOYMENT string = embeddingModel
output INVESTIGATOR_EMBEDDING_MODEL string = embeddingModel
output INVESTIGATOR_EMBEDDING_DIMENSIONS int = 1536
output WEBIQ_RESOURCE_ID string = webIq.id
output WEBIQ_ACCOUNT_NAME string = webIq.name
