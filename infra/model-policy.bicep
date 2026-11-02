targetScope = 'resourceGroup'

param modelAssetIds array

resource approvedModels 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: 'approved-ai-models'
  properties: {
    displayName: 'Only approved AI models can be deployed'
    description: 'Lab 3: only the approved decision and research models can be deployed.'
    policyDefinitionId: tenantResourceId('Microsoft.Authorization/policyDefinitions', 'aafe3651-cb78-4f68-9f81-e7e41509110f')
    parameters: {
      effect: { value: 'Deny' }
      allowedAssetIds: { value: modelAssetIds }
    }
  }
}
