import { useState } from 'react';
import { invoke } from '@tauri-apps/api/core';

const TOOL_PROFILES = {
  wreckhunter: [
    'thermal', 'optical', 'sar', 'triple_lock',
    'download_fox_beaver', 'download_full_lake', 'swot', 'atl13'
  ],
  base: [
    'thermal', 'optical', 'sar', 'triple_lock'
  ],
  research: [
    'thermal', 'optical', 'sar', 'triple_lock', 'swot', 'atl13'
  ]
};

const TOOL_ID_MAPPING = {
  hard_pixel_audit: 'thermal',
  cesarops_gpu: 'thermal',
  andaste_geometry_test: 'optical',
  monster_material_audit: 'sar',
  integrated_forensic_scan: 'triple_lock'
};

const TOOL_PARAMETERS = {
  hard_pixel_audit: {
    name: 'Hard Pixel Audit',
    description: 'Thermal Z-Score analysis for cold-sink detection',
    parameters: [
      { id: 'thermal_zscore_threshold', name: 'Thermal Z-Score Threshold', type: 'number', default: 2.5, min: 1.0, max: 5.0, step: 0.1 },
      { id: 'curvelet_threshold', name: 'Curvelet Threshold', type: 'number', default: 2.0, min: 1.0, max: 5.0, step: 0.1 },
      { id: 'zion_constant', name: 'Zion Constant', type: 'number', default: 1.47, min: 1.0, max: 3.0, step: 0.01 },
      { id: 'depth_threshold_ft', name: 'Depth Threshold (ft)', type: 'number', default: 400, min: 100, max: 1000, step: 50 }
    ]
  },
  cesarops_gpu: {
    name: 'GPU Thermal Processor',
    description: 'wgpu accelerated B10 thermal band processing',
    parameters: [
      { id: 'zion_constant', name: 'Zion Constant', type: 'number', default: 1.47, min: 1.0, max: 3.0, step: 0.01 },
      { id: 'thermal_zscore_threshold', name: 'Z-Score Threshold', type: 'number', default: 2.5, min: 1.0, max: 5.0, step: 0.1 }
    ]
  },
  andaste_geometry_test: {
    name: 'Andaste Geometry Test',
    description: 'Hull geometry verification for 266ft whaleback',
    parameters: [
      { id: 'target_length_ft', name: 'Target Length (ft)', type: 'number', default: 266, min: 200, max: 350, step: 1 },
      { id: 'length_tolerance_ft', name: 'Length Tolerance (ft)', type: 'number', default: 15, min: 5, max: 50, step: 1 },
      { id: 'target_heading_deg', name: 'Target Heading (°)', type: 'number', default: 295, min: 0, max: 360, step: 1 },
      { id: 'heading_tolerance_deg', name: 'Heading Tolerance (°)', type: 'number', default: 15, min: 5, max: 45, step: 1 },
      { id: 'depth_ft', name: 'Target Depth (ft)', type: 'number', default: 180, min: 100, max: 300, step: 10 }
    ]
  },
  monster_material_audit: {
    name: 'Monster Material Audit',
    description: 'Material density analysis for 2000+ ton masses',
    parameters: [
      { id: 'min_mass_tons', name: 'Minimum Mass (tons)', type: 'number', default: 2000, min: 500, max: 50000, step: 100 },
      { id: 'sar_vv_vh_threshold', name: 'SAR VV/VH Threshold', type: 'number', default: 2.0, min: 1.0, max: 5.0, step: 0.1 },
      { id: 'thermal_sink_threshold', name: 'Thermal Sink Threshold', type: 'number', default: 0.7, min: 0.3, max: 1.0, step: 0.05 }
    ]
  },
  integrated_forensic_scan: {
    name: 'Integrated Forensic Scan',
    description: 'Full multi-sensor fusion pipeline',
    parameters: [
      { id: 'thermal_zscore', name: 'Thermal Z-Score', type: 'number', default: 2.5, min: 1.0, max: 5.0, step: 0.1 },
      { id: 'aluminum_ratio', name: 'Aluminum B08/B04 Ratio', type: 'number', default: 1.4, min: 1.0, max: 3.0, step: 0.1 },
      { id: 'sar_coherence', name: 'SAR Coherence Threshold', type: 'number', default: 0.6, min: 0.3, max: 1.0, step: 0.05 },
      { id: 'two_date_tolerance_m', name: 'Two-Date Tolerance (m)', type: 'number', default: 10.0, min: 1.0, max: 50.0, step: 1.0 }
    ]
  }
};

export default function AIAgentConfig({ onAgentConfigured, availableTools }) {
  const [selectedProvider, setSelectedProvider] = useState('qwen_code');
  const [selectedProfile, setSelectedProfile] = useState('wreckhunter');
  const [saveStatus, setSaveStatus] = useState(null);
  const [providerConfigs, setProviderConfigs] = useState({
    qwen_code: {
      apiKey: '',
      model: 'qwen-max'
    },
    kobold_local: {
      endpoint: 'http://localhost:5001/api/v1/generate',
      maxContext: 2048,
      maxTokens: 500
    },
    ollama_local: {
      endpoint: 'http://localhost:11434/api/generate',
      model: 'llama3.1:8b',
      numCtx: 2048
    },
    azure_openai: {
      resource: '',
      deployment: 'gpt-4o-mini',
      apiKey: '',
      apiVersion: '2024-06-01'
    },
    google_vertex: {
      projectId: '',
      location: 'us-central1',
      model: 'gemini-1.5-flash',
      credentials: ''
    },
    huggingface: {
      model: 'meta-llama/Llama-3.1-8B-Instruct',
      apiKey: ''
    },
    groq: {
      apiKey: '',
      model: 'llama-3.1-70b-versatile'
    },
    together: {
      apiKey: '',
      model: 'meta-llama/Llama-3.1-70B-Instruct-Turbo'
    }
  });

  const [toolParams, setToolParams] = useState(() => {
    const params = {};
    Object.entries(TOOL_PARAMETERS).forEach(([toolId, tool]) => {
      params[toolId] = {};
      tool.parameters.forEach(param => {
        params[toolId][param.id] = param.default;
      });
    });
    return params;
  });

  const [testStatus, setTestStatus] = useState(null);
  const [testOutput, setTestOutput] = useState('');

  const handleProviderConfigChange = (providerId, field, value) => {
    setProviderConfigs(prev => ({
      ...prev,
      [providerId]: {
        ...prev[providerId],
        [field]: value
      }
    }));
  };

  const handleToolParamChange = (toolId, paramId, value) => {
    setToolParams(prev => ({
      ...prev,
      [toolId]: {
        ...prev[toolId],
        [paramId]: parseFloat(value)
      }
    }));
  };

  const isToolEnabledForProfile = (toolId) => {
    const parentToolName = TOOL_ID_MAPPING[toolId] || toolId;
    const allowedTools = TOOL_PROFILES[selectedProfile] || [];
    return allowedTools.includes(parentToolName);
  };

  const getVisibleToolParameters = () => {
    return Object.entries(TOOL_PARAMETERS).reduce((acc, [toolId, tool]) => {
      if (isToolEnabledForProfile(toolId)) {
        acc[toolId] = tool;
      }
      return acc;
    }, {});
  };

  const testConnection = async () => {
    setTestStatus('testing');
    setTestOutput('Testing connection...');
    
    const provider = AI_PROVIDERS.find(p => p.id === selectedProvider);
    const config = providerConfigs[selectedProvider];
    
    try {
      // Build endpoint URL
      let endpoint = provider.endpoint;
      if (provider.id === 'azure_openai') {
        endpoint = endpoint.replace('{resource}', config.resource)
                          .replace('{deployment}', config.deployment);
      } else if (provider.id === 'google_vertex') {
        endpoint = endpoint.replace('{projectId}', config.projectId)
                          .replace('{location}', config.location)
                          .replace('{model}', config.model);
      } else if (provider.id === 'huggingface') {
        endpoint = endpoint.replace('{model}', config.model);
      }

      let response;
      if (provider.id === 'qwen_code') {
        response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${config.apiKey}`
          },
          body: JSON.stringify({
            model: config.model,
            input: {
              messages: [
                {
                  role: 'system',
                  content: 'You are the CESAROPS Dispatcher Agent. You know the entire codebase, tools, and Lake Michigan wreck detection pipeline. Test connection by responding with "Qwen Code connected and ready for CESAROPS!"'
                },
                {
                  role: 'user',
                  content: 'Test connection - are you ready to help with wreck detection?'
                }
              ]
            },
            parameters: {
              max_tokens: 100
            }
          })
        });
      } else if (provider.id === 'kobold_local') {
        response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: '### Instruction: You are the CESARops Dispatcher. Test connection. ### Response:',
            max_context_length: config.maxContext,
            max_length: 50,
            stop_sequence: ['###']
          })
        });
      } else if (provider.id === 'ollama_local') {
        response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model: config.model,
            prompt: 'You are the CESARops Dispatcher. Test connection. Respond with "Connected!"',
            stream: false
          })
        });
      } else if (provider.id === 'groq' || provider.id === 'together') {
        response = await fetch(endpoint, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${config.apiKey}`
          },
          body: JSON.stringify({
            model: config.model,
            messages: [{ role: 'user', content: 'You are the CESARops Dispatcher. Test connection. Respond with "Connected!"' }],
            max_tokens: 50
          })
        });
      } else {
        setTestOutput(`Connection test for ${provider.name} - Manual verification required`);
        setTestStatus('success');
        return;
      }

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data = await response.json();
      setTestOutput(`✅ Connection successful!\n\nResponse: ${JSON.stringify(data, null, 2)}`);
      setTestStatus('success');
    } catch (error) {
      setTestOutput(`❌ Connection failed: ${error.message}`);
      setTestStatus('error');
    }
  };

  const saveConfiguration = async () => {
    const config = {
      profile: selectedProfile,
      provider: selectedProvider,
      providerConfig: providerConfigs[selectedProvider],
      toolParameters: toolParams
    };
    
    try {
      // Save to backend
      await invoke('save_agent_config', { config: JSON.stringify(config) });
      
      // Log training data
      const trainingSession = JSON.stringify({
        timestamp: new Date().toISOString(),
        action: 'agent_config_saved',
        profile: selectedProfile,
        provider: selectedProvider,
        config: config
      });
      await invoke('log_training_data', { session: trainingSession });
      
      setSaveStatus('saved');
      onAgentConfigured(config);
      
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err) {
      console.error('Failed to save config:', err);
      setSaveStatus('error');
      // Fallback to local state
      onAgentConfigured(config);
    }
  };

  const currentProvider = AI_PROVIDERS.find(p => p.id === selectedProvider);
  const currentConfig = providerConfigs[selectedProvider];

  return (
    <div className="space-y-6">
      {/* Release Profile Selection */}
      <div className="bg-white/10 backdrop-blur rounded-xl p-6 border border-white/20">
        <h2 className="text-2xl font-bold mb-4">🎯 Release Profile</h2>
        <p className="text-gray-300 mb-6">
          Select the release profile to enable only approved tools. This controls which sensors 
          and analysis methods are available for this instance.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {Object.entries(TOOL_PROFILES).map(([profileName, tools]) => (
            <button
              key={profileName}
              onClick={() => setSelectedProfile(profileName)}
              className={`p-4 rounded-lg border-2 text-left transition-all ${
                selectedProfile === profileName
                  ? 'border-cyan-500 bg-cyan-900/30'
                  : 'border-white/20 bg-white/5 hover:border-white/40'
              }`}
            >
              <h3 className="font-bold capitalize mb-2">{profileName}</h3>
              <p className="text-xs text-gray-400 mb-3">
                {tools.length} tools enabled
              </p>
              <div className="flex flex-wrap gap-1">
                {tools.map(tool => (
                  <span key={tool} className="text-xs px-2 py-1 bg-white/10 rounded">
                    {tool.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Provider Selection */}
      <div className="bg-white/10 backdrop-blur rounded-xl p-6 border border-white/20">
        <h2 className="text-2xl font-bold mb-4">🤖 AI Agent Provider</h2>
        <p className="text-gray-300 mb-6">
          Select an AI provider for the Dispatcher agent. The agent will interpret natural language 
          requests and configure the appropriate tools with optimal parameters.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {AI_PROVIDERS.map(provider => (
            <button
              key={provider.id}
              onClick={() => setSelectedProvider(provider.id)}
              className={`p-4 rounded-lg border-2 text-left transition-all ${
                selectedProvider === provider.id
                  ? 'border-purple-500 bg-purple-900/30'
                  : 'border-white/20 bg-white/5 hover:border-white/40'
              }`}
            >
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-bold">{provider.name}</h3>
                <span className={`text-xs px-2 py-1 rounded ${
                  provider.type === 'local' 
                    ? 'bg-green-900/50 text-green-300' 
                    : 'bg-blue-900/50 text-blue-300'
                }`}>
                  {provider.type === 'local' ? '🏠 Local' : '☁️ Cloud'}
                </span>
              </div>
              <p className="text-sm text-gray-400">{provider.description}</p>
              <p className="text-xs text-gray-500 mt-2">Default: {provider.defaultModel}</p>
            </button>
          ))}
        </div>
      </div>

      {/* Provider Configuration */}
      <div className="bg-white/10 backdrop-blur rounded-xl p-6 border border-white/20">
        <h2 className="text-2xl font-bold mb-4">⚙️ Provider Configuration</h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {currentProvider?.configFields?.map(field => (
            <div key={field}>
              <label className="block text-sm font-medium mb-2 capitalize">
                {field.replace(/([A-Z])/g, ' $1').trim()}
              </label>
              {field.toLowerCase().includes('key') || field.toLowerCase().includes('credentials') ? (
                <input
                  type="password"
                  value={currentConfig[field] || ''}
                  onChange={(e) => handleProviderConfigChange(selectedProvider, field, e.target.value)}
                  className="w-full px-4 py-2 bg-white/10 border border-white/20 rounded-lg text-white"
                  placeholder={`Enter your ${field}`}
                />
              ) : field.toLowerCase().includes('endpoint') || field.toLowerCase().includes('resource') || field.toLowerCase().includes('project') || field.toLowerCase().includes('model') || field.toLowerCase().includes('deployment') || field.toLowerCase().includes('location') ? (
                <input
                  type="text"
                  value={currentConfig[field] || ''}
                  onChange={(e) => handleProviderConfigChange(selectedProvider, field, e.target.value)}
                  className="w-full px-4 py-2 bg-white/10 border border-white/20 rounded-lg text-white"
                  placeholder={field}
                />
              ) : (
                <input
                  type="number"
                  value={currentConfig[field] || ''}
                  onChange={(e) => handleProviderConfigChange(selectedProvider, field, parseInt(e.target.value))}
                  className="w-full px-4 py-2 bg-white/10 border border-white/20 rounded-lg text-white"
                />
              )}
            </div>
          ))}
        </div>

        {/* Test Connection */}
        <div className="mt-6 flex items-center gap-4">
          <button
            onClick={testConnection}
            className="px-6 py-3 bg-blue-600 hover:bg-blue-500 rounded-lg font-semibold transition-all"
          >
            🧪 Test Connection
          </button>
          {testStatus === 'testing' && <span className="text-yellow-300">Testing...</span>}
          {testStatus === 'success' && <span className="text-green-400">✅ Connected</span>}
          {testStatus === 'error' && <span className="text-red-400">❌ Failed</span>}
        </div>

        {testOutput && (
          <div className="mt-4 p-4 bg-black/30 rounded-lg">
            <pre className="text-sm font-mono whitespace-pre-wrap">{testOutput}</pre>
          </div>
        )}
      </div>

      {/* Tool Parameter Configuration */}
      <div className="bg-white/10 backdrop-blur rounded-xl p-6 border border-white/20">
        <h2 className="text-2xl font-bold mb-4">🔧 Tool Parameter Mapping</h2>
        <p className="text-gray-300 mb-6">
          Configure the default parameters for each tool enabled in the selected profile. 
          The AI agent will use these as baseline values and may adjust them based on the user's request.
        </p>

        {Object.keys(getVisibleToolParameters()).length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            ⚠️ No tools available in the '{selectedProfile}' profile
          </div>
        ) : (
          <div className="space-y-6">
            {Object.entries(getVisibleToolParameters()).map(([toolId, tool]) => (
              <div key={toolId} className="border border-white/10 rounded-lg p-4">
                <h3 className="text-lg font-bold mb-2">{tool.name}</h3>
                <p className="text-sm text-gray-400 mb-4">{tool.description}</p>
                
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                  {tool.parameters.map(param => (
                    <div key={param.id}>
                      <label className="block text-sm font-medium mb-2">
                        {param.name}
                      </label>
                      <input
                        type="number"
                        value={toolParams[toolId][param.id]}
                        onChange={(e) => handleToolParamChange(toolId, param.id, e.target.value)}
                        min={param.min}
                        max={param.max}
                        step={param.step}
                        className="w-full px-3 py-2 bg-white/10 border border-white/20 rounded-lg text-white"
                      />
                      <p className="text-xs text-gray-500 mt-1">
                        Range: {param.min} - {param.max}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Agent Prompt Template */}
      <div className="bg-white/10 backdrop-blur rounded-xl p-6 border border-white/20">
        <h2 className="text-2xl font-bold mb-4">📝 Agent System Prompt</h2>
        <p className="text-gray-300 mb-4">
          This prompt instructs the AI on how to interpret user requests and configure tools.
          {selectedProvider === 'qwen_code' && ' (Qwen Code already knows the CESAROPS codebase!)'}
        </p>
        <textarea
          className="w-full h-48 px-4 py-3 bg-black/30 border border-white/20 rounded-lg text-white font-mono text-sm"
          defaultValue={`You are the CESAROPS Dispatcher Agent. Your job is to:

1. INTERPRET the user's natural language request about wreck detection in Lake Michigan
2. ASK clarifying questions if needed (target type, location, depth, size)
3. DEFINE a bounding box for the search area (lat/lon bounds)
4. SELECT the appropriate tools from available options:
   - hard_pixel_audit: Thermal Z-Score analysis for cold-sink detection
   - cesarops_gpu: GPU-accelerated thermal processing
   - andaste_geometry_test: Verify 266ft whaleback at 295° heading
   - monster_material_audit: Detect 2000+ ton steel masses
   - integrated_forensic_scan: Full multi-sensor fusion
5. CONFIGURE tool parameters based on the target characteristics
6. OUTPUT a JSON configuration for the Rust engine to execute

CESAROPS CONTEXT:
- Zion Constant: 1.47x (for 180ft depth scaling)
- Triple-Lock Fusion: Optical + Thermal + SAR/SWOT
- Target Profiles: Andaste (310ft steel), Flight 2501 (aluminum DC-4)
- Search Area: Lake Michigan South (42-43°N, 87-88°W)

Example interaction:
User: "Find the Andaste wreck"
You: "Searching for SS Andaste - 310ft whaleback freighter at 180ft depth. Based on historical records, I'll search the Zion Trench area with these parameters:
- Bounding Box: 42.4°N to 42.5°N, 87.5°W to 87.6°W
- Tools: andaste_geometry_test (266ft ±15ft, 295°±15°), hard_pixel_audit (Z>2.5)
- Depth range: 175-185ft

Execute this search?"`}
        />
      </div>

      {/* Save Configuration */}
      <div className="flex justify-end gap-4 items-center">
        <button
          onClick={async () => {
            try {
              const configJson = await invoke('load_agent_config');
              const config = JSON.parse(configJson);
              setSelectedProvider(config.provider);
              if (config.providerConfig) {
                setProviderConfigs(prev => ({ ...prev, [config.provider]: config.providerConfig }));
              }
              if (config.toolParameters) {
                setToolParams(config.toolParameters);
              }
              alert('Configuration loaded successfully!');
            } catch (err) {
              alert('No saved configuration found: ' + err);
            }
          }}
          className="px-6 py-4 bg-white/10 hover:bg-white/20 rounded-xl font-bold text-lg transition-all"
        >
          📂 Load Config
        </button>
        <button
          onClick={saveConfiguration}
          className="px-8 py-4 bg-gradient-to-r from-purple-600 to-blue-600 hover:from-purple-500 hover:to-blue-500 rounded-xl font-bold text-lg shadow-lg transition-all"
        >
          💾 Save Agent Configuration
        </button>
        {saveStatus === 'saved' && (
          <span className="text-green-400 font-semibold">✅ Saved!</span>
        )}
        {saveStatus === 'error' && (
          <span className="text-red-400 font-semibold">❌ Save failed</span>
        )}
      </div>
    </div>
  );
}
