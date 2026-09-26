export type ResponseType = 'api' | 'rag' | 'llm' | 'human';

export interface SubcategoryConfig {
  id: string;
  tenant_id: string;
  sub_category: string;
  is_active: boolean;
  response_type: ResponseType;
  llm_prompt_override?: string;
  human_contact?: string;
  agent_type?: string;
  created_at: string;
  updated_at?: string;
}

export interface SubcategoryConfigCreate {
  tenant_id: string;
  sub_category: string;
  is_active: boolean;
  response_type: ResponseType;
  agent_type?: string;
  llm_prompt_override?: string;
  human_contact?: string;
}

export interface SubcategoryConfigUpdate {
  is_active?: boolean;
  response_type?: ResponseType;
  llm_prompt_override?: string;
  human_contact?: string;
  agent_type?: string;
}

export interface TenantFile {
  file_id: string;
  tenant_id: string;
  name: string;
  file_type: string;
  url: string;
  agent_type: string;
  embedding_status: string;
  chunk_count: number;
  created_at: string;
  size_bytes: number;
}

export interface Provider {
  provider_id: string;
  name: string;
  code: string;
  category: string;
}

export interface ApiKeyOut {
  key_id: string;
  label: string;
  provider_id: string;
  provider_name: string;
  agent_type: string;
  api_url: string;
  http_method: string;
  headers_template?: any;
  payload_template?: any;
  param_mapping?: any;
  result_path?: string;
  priority: number;
  timeout_ms: number;
  retry_count: number;
  is_active: boolean;
  created_at: string;
}

export interface ApiKeyCreate {
  label: string;
  provider_id: string;
  agent_type: string;
  api_url: string;
  api_key: string;
  http_method: string;
  headers_template?: any;
  payload_template?: any;
  param_mapping?: any;
  result_path?: string;
  priority: number;
  timeout_ms: number;
  retry_count: number;
}
export interface ParamMappingEntry {
  entityType: string;  
  entityIndex: number; 
  param: string;
  transform: string;
  default: string;
  optional: boolean;
}