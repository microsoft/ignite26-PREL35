targetScope = 'resourceGroup'

param apimName string
param foundryAccountName string
param foundryProjectName string
param projectPrincipalId string
param callerPrincipalIds array = []

@minValue(0)
@maxValue(75)
param deadlineSeconds int = 75

var principals = union(callerPrincipalIds, [projectPrincipalId])
var claims = join(map(principals, id => '<value>${id}</value>'), '')
var authPolicy = replace(loadTextContent('systemone-auth-policy.xml'), '__CALLERS__', claims)
var assessmentPolicy = replace(replace(replace(
  loadTextContent('systemone-assessment-policy.xml'),
  '__AUTH__', authPolicy),
  '__FOUNDRY_ENDPOINT__', 'https://${foundryAccountName}.services.ai.azure.com'),
  '__DEADLINE__', string(deadlineSeconds))
var mcpEndpoint = 'https://${apimName}.azure-api.net/alert-disposition-mcp/mcp'

resource apim 'Microsoft.ApiManagement/service@2025-09-01-preview' existing = {
  name: apimName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundry
  name: foundryProjectName
}

resource rules 'Microsoft.ApiManagement/service/namedValues@2025-09-01-preview' = {
  parent: apim
  name: 'systemone-triage-rules'
  properties: {
    displayName: 'systemone-triage-rules'
    secret: false
    value: loadTextContent('../src/prompts/decision1.md')
  }
}

// Keep the tested resource names and paths so existing clients and connections continue to work.
resource decisionApi 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'alert-disposition-rest'
  properties: {
    displayName: 'Decision-1 assessments'
    description: 'Read-only, independent assessments of complete permitted case files.'
    path: 'alert-disposition'
    protocols: ['https']
    subscriptionRequired: false
    type: 'http'
    format: 'openapi+json'
    value: loadTextContent('systemone-openapi.json')
  }
}

resource decisionPolicy 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: decisionApi
  name: 'policy'
  properties: { format: 'rawxml', value: assessmentPolicy }
  dependsOn: [rules]
}

resource mcpApi 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'alert-disposition-mcp'
  properties: {
    displayName: 'Decision-1 MCP server'
    path: 'alert-disposition-mcp'
    protocols: ['https']
    subscriptionRequired: false
    type: 'mcp'
  }
}

resource mcpTool 'Microsoft.ApiManagement/service/apis/tools@2025-09-01-preview' = {
  parent: mcpApi
  name: 'asses_next_step'
  properties: {
    displayName: 'asses_next_step'
    description: 'Assess 1..50 complete permitted case files with fixed bank rules. Preserve all records. Decision-1 chooses dispositions; service failures have null dispositions. Read-only recommendations, not case actions.'
    operationId: '${decisionApi.id}/operations/asses_next_step'
  }
  dependsOn: [decisionPolicy, mcpPolicy]
}

resource mcpPolicy 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: mcpApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(loadTextContent('systemone-mcp-policy.xml'), '__AUTH__', authPolicy)
  }
}

resource connection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: project
  name: 'alert-disposition-mcp'
  properties: {
    category: 'RemoteTool'
    target: mcpEndpoint
    #disable-next-line BCP036
    authType: 'ProjectManagedIdentity'
    #disable-next-line BCP037
    audience: 'https://ai.azure.com'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      Audience: 'https://ai.azure.com'
    }
  }
  dependsOn: [mcpTool, mcpPolicy]
}

resource agentsApi 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' existing = {
  parent: apim
  name: 'agents'
}

resource agentOperation 'Microsoft.ApiManagement/service/apis/operations@2025-09-01-preview' = {
  parent: agentsApi
  name: 'triage_systemone'
  properties: {
    displayName: 'triage_systemone agent'
    method: 'POST'
    urlTemplate: '/triage_systemone/responses'
  }
}

resource agentOperationPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2025-09-01-preview' = {
  parent: agentOperation
  name: 'policy'
  properties: { format: 'rawxml', value: loadTextContent('systemone-agent-policy.xml') }
}

output REST_URL string = 'https://${apimName}.azure-api.net/alert-disposition/asses_next_step'
output MCP_URL string = mcpEndpoint
output AGENT_URL string = 'https://${apimName}.azure-api.net/agents/triage_systemone'
output CONNECTION_NAME string = connection.name
