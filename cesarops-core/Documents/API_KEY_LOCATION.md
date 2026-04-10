# 🔑 CESAROPS API Key Locations

## Qwen Code (Alibaba DashScope) - PRIMARY AI

**Location in App:**
- Tab: **🤖 AI Agent Config**
- Provider: **Qwen Code (Alibaba)** (top of list)
- Field: **API Key**

**How to Get:**
1. Go to: https://modelstudio.console.alibabacloud.com/us-east-1
2. Sign in / Create Alibaba Cloud account
3. Left sidebar → **API-KEY Management**
4. Click **Create API Key**
5. Copy the key (starts with `sk-...`)

**Free Tier:**
- Qwen-Max: ~1,000-2,000 tokens/day
- Qwen-Plus: ~5,000 tokens/day
- Enough for development!

**In Code:**
```javascript
// tauri-app/src/components/AIAgentConfig.jsx
const [providerConfigs, setProviderConfigs] = useState({
  qwen_code: {
    apiKey: '',  // ← PASTE KEY HERE
    model: 'qwen-max'
  },
  // ...
});
```

**Config File (after save):**
```
cesarops-wreckhunter build/agent_config.json
```

---

## Other Providers (Optional)

### KoboldCPP (Local - Free)
- Endpoint: `http://localhost:5001/api/v1/generate`
- No API key needed

### Ollama (Local - Free)
- Endpoint: `http://localhost:11434/api/generate`
- No API key needed

### Groq Cloud
- Get key: https://console.groq.com/keys
- Fast inference (LPU acceleration)

### Together AI
- Get key: https://api.together.ai/
- Wide model selection

---

## 🎯 Recommended Setup

**For Development:**
1. **Qwen Code** (primary) - Deep CESAROPS context awareness
2. **KoboldCPP Local** (backup) - Free, private, unlimited

**For Production:**
- Qwen Code for AI dispatch
- Local KoboldCPP for fallback
- Xenon server for heavy processing

---

## ⚠️ Security Notes

- API keys saved in `agent_config.json` (local only)
- **DO NOT commit** `agent_config.json` to git
- Keys stored in plaintext on local machine
- For production: Use environment variables or secure vault

---

**Last Updated:** 2026-04-02
**Status:** Qwen integration ready - needs API key pasted in UI
