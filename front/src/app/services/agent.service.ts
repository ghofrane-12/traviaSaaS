// services/agent.service.ts
import { Injectable, NgZone } from '@angular/core';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Observable } from 'rxjs';
import { UserService } from './user.service';
import { environment } from '../environments/environment';
import { Auth } from '@angular/fire/auth';
import { CrossSellProposal } from './result.service';
import { ResultsCacheService } from './results-cache.service';

export interface FormField {
  name: string;
  label: string;
  type: string;
  required: boolean;
  placeholder?: string;
  min?: number;
  max?: number;
  value?: any;
  format?: string;
  options?: string[];
  fields?: FormField[];
}

export interface AgentForm {
  type: string;
  title: string;
  description: string;
  fields: FormField[];
}

export interface AgentMessageResponse {
  reply: string;
  category?: string;
  deberta_category?: string;
  sub_category?: string;
  language?: string;
  score?: number;
  entities?: { type: string; value: string | null }[];
  complexity?: number;
  session_id?: string;
  tenant_id?: string;
  user_id?: string;
  status?: 'success' | 'need_more_info' | 'rag_only' | 'gemini_fallback' | 'api_error' | 'blocked' | 'rejected' | 'error' | 'waiting_for_form';  source?: string;
  type?: string;
  query?: any;
  message?: string;
  form?: AgentForm;
  required_fields?: any[];
  offers?: any[];
  results?: any[];
  transports?: any[];
  rag_context?: string;
  steps?: string[];
  suggestions?: any[];
  loyalty_summary?: {
    tier?: string;
    points_earned?: number;
    [key: string]: any;
  };
  follow_up?: string;
  pending_forms?: Array<{
    status:       'need_more_info';
    sub_category: string;
    form?:        AgentForm;
    steps?:       string[];
    message?:     string;
    entities?:    { type: string; value: string }[];
  }>;
  info?: {
    summary?: string;
    suggestion?: string;
    advice?: string;
    [key: string]: any;
  };
}

// =========================================================================
// STREAM CALLBACKS
// =========================================================================

export interface StreamCallbacks {
  onWord:           (word: string) => void;

  onJson:           (data: any) => void;

  onDone:           () => void;

  onError?:         (error: any) => void;

  onStatus?:        (step: string, message: string, subCategory?: string) => void;


  onSegmentDone?:   (event: SegmentDoneEvent) => void;

  onSegmentForm?:   (event: SegmentFormEvent) => void;

  onStreamDone?:    (event: StreamDoneEvent) => void;

  onSegmentResult?: (segmentKey: string, subCategory: string, data: any) => void;
}


export interface SegmentDoneEvent {
  type:        'segment_done';
  seg_id:      string;
  result_type: 'info_results' | 'flight_search_results' | 'hotel_search_results'
             | 'activity_search_results' | 'restaurant_search_results'
             | 'specialty_search_results' | 'transport_search_results'
             | 'error' | 'human_required'| 'management_question'; 
  message:     string;
  follow_up?:  string;
  suggestions?: any[];
  sections?:   any[];
  actions?:    any[];
  offers?:     any[]; 
  results?:    any[];
  query?:      any;
  label?:      string;
  show_ticket_form?: boolean;
}

export interface SegmentFormEvent {
  type:         'segment_form';
  seg_id:       string;
  sub_category: string;
  sub_categories?: string[];   
  is_merged?:    boolean; 
  message:      string;
  form?:        AgentForm;
  steps?:       any[];
  entities?:    any[];
  language?:    string;
}

export interface StreamDoneEvent {
  type:            'stream_done';
  loyalty_summary: any;
  follow_up:       string;
  total_segments:  number;
  suggestions?:    any[];  
  cross_sell_proposals?: CrossSellProposal[]; 
  error?:          string;
}

export interface ClarificationContext {
  clarification_pending:   boolean;
  clarification_subcats:   any[];
  clarification_segment:   string;
  clarification_accepted?: any[];
  clarification_entities?: any;
  clarification_choice?:   { id: number; label: string } | null;
  ambiguous_queue?:        any[];  
}
export interface CrossSellContext {
  cross_sell:     true;
  classification: Array<{ sub_category: string; category: string; confidence: number }>;
  entities:       Array<{ type: string; value: string }>;
  language?:      string;
}
const apiUrl = environment.apiUrl;

@Injectable({ providedIn: 'root' })
export class AgentService {
  private baseUrl    = `${apiUrl}chat/`;
  private streamUrl  = `${apiUrl}chat/stream`;
  private apiUrl     = apiUrl;
  private lastSegmentFormLanguage: string = '';

  private lastLanguage: string = sessionStorage.getItem('last_language') || 'query_fr';  private currentSessionId: string = '';
  private currentTenantId:  string = '';

  private readonly DEFAULT_TENANT_ID = 'a1b2c3d4-0001-0000-0000-000000000001';

  constructor(
    private http:        HttpClient,
    private userService: UserService,
    private auth:        Auth,
    private ngZone:      NgZone,
    private resultsCache: ResultsCacheService,
  
  ) {}

  getLastLanguage(): string { return this.lastLanguage; }

  setLastLanguage(language: string): void {
    if (language) {
      const mapping: Record<string, string> = {
        'fr':          'query_fr',
        'ar':          'query_ar',
        'en':          'query_en',
        'derja':       'query_derja_a',
        'derja_latin': 'query_derja_l',
        'derja_arabe': 'query_derja_a',
      };
      this.lastLanguage = language.startsWith('query_') 
        ? language 
        : (mapping[language] || 'query_fr');
        sessionStorage.setItem('last_language', this.lastLanguage);
      console.log(`[AgentService] Language set to: ${this.lastLanguage}`);
    }
  }

private getToken(): Promise<string | null> {
  return new Promise((resolve) => {
    if (this.auth.currentUser) {
      resolve(this.auth.currentUser.getIdToken());
      return;
    }
    const unsubscribe = this.auth.onAuthStateChanged((user) => {
      unsubscribe();
      resolve(user ? user.getIdToken() : null);
    });
    setTimeout(() => { unsubscribe(); resolve(null); }, 5000);
  });
}

  getTenantId(): string {
    if (this.currentTenantId) return this.currentTenantId;
    const user = this.userService.session();
    if (user?.tenant_id)             { this.currentTenantId = user.tenant_id;             return this.currentTenantId; }
    const userTenant = this.userService.tenantId();
    if (userTenant)                  { this.currentTenantId = userTenant;                  return this.currentTenantId; }
    const storedTenant = sessionStorage.getItem('visitor_tenant_id');
    if (storedTenant)                { this.currentTenantId = storedTenant;                return this.currentTenantId; }
    return this.DEFAULT_TENANT_ID;
  }

  setTenantId(tenantId: string): void {
    this.currentTenantId = tenantId;
    sessionStorage.setItem('visitor_tenant_id', tenantId);
  }

  getSessionId(): string {
    if (this.currentSessionId) {
      return this.currentSessionId;
    }
    
    const user = this.userService.session();
    if (user) {
      const storedKey = `session_${user.user_id}_${user.tenant_id}`;
      const stored = sessionStorage.getItem(storedKey);
      
      if (stored) {
        this.currentSessionId = stored;
        console.log('[AgentService] Loaded existing session:', this.currentSessionId);
        return this.currentSessionId;
      }
      
      this.currentSessionId = `${user.user_id}__${user.tenant_id}__${Date.now()}`;
      sessionStorage.setItem(storedKey, this.currentSessionId);
      console.log('[AgentService] Created new session:', this.currentSessionId);
      return this.currentSessionId;
    }
    
    let sessionId = sessionStorage.getItem('visitor_session_id');
    if (!sessionId) {
      const visitorId = this.userService.getVisitorId();
      const today = new Date().toISOString().split('T')[0].replace(/-/g, '');
      sessionId = `${visitorId}__${today}`;
      sessionStorage.setItem('visitor_session_id', sessionId);
    }
    this.currentSessionId = sessionId;
    return this.currentSessionId;
  }

  setSessionId(sessionId: string): void {
    console.log('[AgentService] setSessionId:', sessionId);
    this.currentSessionId = sessionId;
    
    const user = this.userService.session();
    if (user) {
      sessionStorage.setItem(`session_${user.user_id}_${user.tenant_id}`, sessionId);
    } else {
      sessionStorage.setItem('visitor_session_id', sessionId);
    }
    sessionStorage.setItem('current_session_id', sessionId);
  }

  syncSessionIdFromComponent(sessionId: string): void {
    if (this.currentSessionId !== sessionId) {
      console.log('[AgentService] Syncing session from component:', sessionId);
      this.setSessionId(sessionId);
    }
  }

  public getUserId(): string {
    const user = this.userService.session();
    return user ? user.user_id : this.userService.getVisitorId();
  }

  setVisitorTenant(tenantId: string): void {
    this.currentTenantId = tenantId;
    sessionStorage.setItem('visitor_tenant_id', tenantId);
  }

  sendMessage(message: string): Observable<AgentMessageResponse[]> {
    const user      = this.userService.session();
    const tenantId  = user?.tenant_id || this.getTenantId();
    const sessionId = this.getSessionId();
    const userId    = this.getUserId();
    return this.http.post<AgentMessageResponse[]>(this.baseUrl, {
      text: message, user_id: userId,
      tenant_id: tenantId, session_id: sessionId,
      language: this.getLastLanguage()
    });
  }

async getUserSessions(tenantId: string): Promise<any> {
  const token = await this.getToken();
  if (!token) return { sessions: [] }; 
  
  const headers: Record<string, string> = {
    'Authorization': `Bearer ${token}`
  };
  try {
    const res = await fetch(`${this.baseUrl}sessions?tenant_id=${tenantId}`, 
      { method: 'GET', headers });
    return res.ok ? res.json() : { sessions: [] };
  } catch { 
    return { sessions: [] }; 
  }
}

  async deleteConversation(sessionId: string, tenantId: string): Promise<boolean> {
    const token   = await this.getToken();
    const headers: Record<string, string> = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    try {
      const res = await fetch(`${this.baseUrl}conversation/${sessionId}?tenant_id=${tenantId}`,
        { method: 'DELETE', headers });
      return res.ok;
    } catch { return false; }
  }

  async renameConversation(sessionId: string, tenantId: string, newTitle: string): Promise<boolean> {
    const token   = await this.getToken();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    try {
      const res = await fetch(
        `${this.baseUrl}conversation/${sessionId}/rename?tenant_id=${tenantId}`,
        { method: 'PUT', headers, body: JSON.stringify({ title: newTitle }) }
      );
      return res.ok;
    } catch { return false; }
  }

  // =========================================================================
  // STREAMING
  // =========================================================================

  async sendMessageStream(
    message:              string,
    callbacks:            StreamCallbacks,
    clarificationContext?: ClarificationContext | CrossSellContext  // ← étendre
  ): Promise<void> {
    const requestLanguage = this.getLastLanguage();
    const { onWord, onJson, onDone, onError } = callbacks;
    const token     = await this.getToken();
    const user      = this.userService.session();
    const tenantId  = user?.tenant_id || this.getTenantId();
    const sessionId = this.getSessionId();
    const userId    = this.getUserId();

    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const payload: any = {
      text:       message,
      tenant_id:  tenantId,
      session_id: sessionId,
      user_id:    userId,
    };

    if (clarificationContext && 'clarification_pending' in clarificationContext) {
  const ctx = clarificationContext as ClarificationContext;
  payload.clarification_pending  = true;
  payload.clarification_subcats  = ctx.clarification_subcats;
  payload.clarification_segment  = ctx.clarification_segment;
  payload.clarification_accepted = ctx.clarification_accepted ?? [];
  payload.clarification_entities = ctx.clarification_entities ?? {};
  payload.ambiguous_queue        = ctx.ambiguous_queue ?? [];

  if (ctx.clarification_choice) {
    payload.clarification_choice = ctx.clarification_choice;
  }
} else if (clarificationContext && 'cross_sell' in clarificationContext) {
  const ctx = clarificationContext as CrossSellContext;
  payload.is_cross_sell  = true;              
  payload.classification = ctx.classification;
  payload.entities       = ctx.entities;
  payload.label          = message;  
  payload.language       = ctx.language || this.getLastLanguage();
}

    try {
      const response = await fetch(this.streamUrl, {
        method: 'POST', headers, body: JSON.stringify(payload)
      });

      const reader  = response.body!.getReader();
      const decoder = new TextDecoder();
      let   buffer  = '';
      let   isDone  = false;

      const processLine = (line: string): void => {
        if (!line.startsWith('data: ')) return;
        const data = line.slice(6).trim();
        if (!data) return;

        if (data === '[DONE]') {
          if (!isDone) {
            isDone = true;
            this.ngZone.run(() => onDone());
          }
          return;
        }

        try {
          const parsed = JSON.parse(data);

          if (parsed.type === 'text') {
            this.ngZone.run(() => onWord(parsed.word));
            return;
          }

          if (parsed.type === 'json') {
            this.ngZone.run(() => onJson(parsed.data));
            return;
          }

          if (parsed.type === 'status' || parsed.type === 'agent_start') {
            if (callbacks.onStatus) {
              this.ngZone.run(() =>
                callbacks.onStatus!(parsed.step, parsed.message ?? '', parsed.sub_category)
              );
            }
            return;
          }

          if (parsed.type === 'segment_done') {
            if (callbacks.onSegmentDone) {
              this.ngZone.run(() =>
                callbacks.onSegmentDone!(parsed as SegmentDoneEvent)
              );
            }
            return;
          }

          if (parsed.type === 'segment_form') {
              if (parsed.language) {
    this.setLastLanguage(parsed.language);
    this.lastSegmentFormLanguage = parsed.language;
  }
            if (callbacks.onSegmentForm) {
              this.ngZone.run(() =>
                callbacks.onSegmentForm!(parsed as SegmentFormEvent)
              );
            }
            return;
          }
          if (parsed.type === 'cross_sell_proposals') {
            (callbacks as any)._crossSellProposals = parsed.proposals;
            return;
          }
          if (parsed.type === 'stream_done') {
          if (parsed.language) {
            this.setLastLanguage(parsed.language);  
              this.lastSegmentFormLanguage = parsed.language;
          }
          if (!parsed.cross_sell_proposals) {
            parsed.cross_sell_proposals = (callbacks as any)._crossSellProposals || [];
          }
          if (callbacks.onStreamDone) {
            this.ngZone.run(() =>
              callbacks.onStreamDone!(parsed as StreamDoneEvent)
            );
          }
            const sessionId = this.getSessionId();
            if (sessionId) {
              setTimeout(async () => {
                const event = new CustomEvent('save-results-cache', {
                  detail: { sessionId }
                });
                window.dispatchEvent(event);
              }, 500);
            }
          return;
        }

          if (parsed.type === 'segment_result') {
            if (callbacks.onSegmentResult) {
              this.ngZone.run(() =>
                callbacks.onSegmentResult!(parsed.segment_key, parsed.sub_category, parsed.data)
              );
            }
            return;
          }

        } catch (e) {
          console.warn('[AgentService] Parse error:', data.slice(0, 80), e);
        }
      };

      const pump = async (): Promise<void> => {
        const { done, value } = await reader.read();

        if (done) {
          if (buffer.trim()) {
            buffer.split('\n').forEach(l => processLine(l.trim()));
          }
          if (!isDone) {
            isDone = true;
            this.ngZone.run(() => onDone());
          }
          return;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        lines.forEach(l => processLine(l.trim()));

        await pump();
      };

      await pump();

    } catch (error) {
      console.error('[AgentService] Stream error:', error);
      this.ngZone.run(() => {
        if (onError) onError(error);
        else { onWord('Erreur de connexion. Veuillez réessayer.'); onDone(); }
      });
    }
  }

  // =========================================================================
  // FORMULAIRES
  // =========================================================================

  getLastSegmentFormLanguage(): string {
    return this.lastSegmentFormLanguage || this.lastLanguage;
  }

  async sendFormResponse(
    formData:        Record<string, any>,
    pendingSegments: Record<string, any>,
    callbacks:       StreamCallbacks,
    language?:       string,
  ): Promise<void> {
    const token     = await this.getToken();
    const user      = this.userService.session();
    const tenantId  = user?.tenant_id || this.getTenantId();
    const sessionId = this.getSessionId();
    const userId    = this.getUserId();

    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const payload = {
      text:             '',
      tenant_id:        tenantId,
      session_id:       sessionId,
      user_id:          userId,
      language: language || this.lastSegmentFormLanguage || 'query_fr',
      is_form_response: true,
      form_data:        formData,
      pending_segments: pendingSegments,
      
    };
    console.log('[sendFormResponse] language envoyé:', this.lastSegmentFormLanguage);


    try {
      const response = await fetch(this.streamUrl, {
        method: 'POST', headers, body: JSON.stringify(payload)
      });

      const reader  = response.body!.getReader();
      const decoder = new TextDecoder();
      let   buffer  = '';
      let   isDone  = false;

      const processLine = (line: string): void => {
        if (!line.startsWith('data: ')) return;
        const data = line.slice(6).trim();
        if (!data) return;
        if (data === '[DONE]') {
          if (!isDone) { isDone = true; this.ngZone.run(() => callbacks.onDone()); }
          return;
        }
        try {
          const parsed = JSON.parse(data);
          if (parsed.type === 'text'         && callbacks.onWord)        this.ngZone.run(() => callbacks.onWord(parsed.word));
          if (parsed.type === 'json'         && callbacks.onJson)        this.ngZone.run(() => callbacks.onJson(parsed.data));
          if (parsed.type === 'segment_done' && callbacks.onSegmentDone) this.ngZone.run(() => callbacks.onSegmentDone!(parsed));
          if (parsed.type === 'segment_form' && callbacks.onSegmentForm) this.ngZone.run(() => callbacks.onSegmentForm!(parsed));
          if (parsed.type === 'status'       && callbacks.onStatus)      this.ngZone.run(() => callbacks.onStatus!(parsed.step, parsed.message ?? '', parsed.sub_category));
          if (parsed.type === 'cross_sell_proposals') {
            (callbacks as any)._crossSellProposals = parsed.proposals;
          }

          if (parsed.type === 'stream_done' && callbacks.onStreamDone) {
            if (!parsed.cross_sell_proposals?.length) {
              parsed.cross_sell_proposals = (callbacks as any)._crossSellProposals || [];
            }
           
             console.log('[stream_done] lastSegmentFormLanguage =', this.lastSegmentFormLanguage);

            this.ngZone.run(() => callbacks.onStreamDone!(parsed));

            const sessionId = this.getSessionId();
            if (sessionId) {
              setTimeout(async () => {
                const event = new CustomEvent('save-results-cache', {
                  detail: { sessionId }
                });
                window.dispatchEvent(event);
              }, 500);
            }
          }

        } catch (e) {
          console.warn('[AgentService] sendFormResponse parse error:', data.slice(0, 80), e);
        }
      };

      const pump = async (): Promise<void> => {
        const { done, value } = await reader.read();
        if (done) {
          if (buffer.trim()) buffer.split('\n').forEach(l => processLine(l.trim()));
          if (!isDone) { isDone = true; this.ngZone.run(() => callbacks.onDone()); }
          return;
        }
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        lines.forEach(l => processLine(l.trim()));
        await pump();
      };

      await pump();

    } catch (error) {
      console.error('[AgentService] sendFormResponse error:', error);
      this.ngZone.run(() => {
        if (callbacks.onError) callbacks.onError(error);
        else callbacks.onDone();
      });
    }
  }

async sendStayForm(
  formData: {
    city: string; check_in: string; check_out: string;
    guests: number; rooms: number;
    price_min?: number; price_max?: number;
    stars?: string; sort_by?: string; order?: string;
  },
  callbacks: StreamCallbacks
): Promise<void> {
  const tenantId = this.getTenantId();
  const sessionId = this.getSessionId();
  const userId = this.getUserId();

  const entities: { type: string; value: string }[] = [
    { type: 'LOC',   value: formData.city },
    { type: 'DATE',  value: formData.check_in },
    { type: 'DATE',  value: formData.check_out },
    { type: 'PRICE', value: String(formData.guests) },
    { type: 'ORG',   value: String(formData.rooms) },
  ];
  if (formData.price_min) entities.push({ type: 'PRICE', value: String(formData.price_min) });
  if (formData.price_max) entities.push({ type: 'PRICE', value: String(formData.price_max) });
  if (formData.stars)     entities.push({ type: 'ORG',   value: formData.stars });

  const token = await this.getToken();
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;

  const payload = {
    text: '',
    entities,
    classification: [{ sub_category: 'Hotel Booking', category: 'Reservation', score: 1.0, confidence: 'validated' }],
    language: this.getLastLanguage(),
    user_id: userId,
    tenant_id: tenantId,
    session_id: sessionId
  };

  try {
    const response = await fetch(this.streamUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload)
    });

    const reader = response.body!.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const processLine = (line: string) => {
      if (!line.startsWith('data: ')) return;
      const data = line.slice(6).trim();
      if (!data || data === '[DONE]') return;

      try {
        const parsed = JSON.parse(data);

        if (parsed.type === 'segment_done' && callbacks.onSegmentDone) {
          this.ngZone.run(() => callbacks.onSegmentDone!(parsed));
        } else if (parsed.type === 'segment_form' && callbacks.onSegmentForm) {
            if (parsed.language) {
    this.setLastLanguage(parsed.language);
    this.lastSegmentFormLanguage = parsed.language;
  }
          this.lastSegmentFormLanguage = this.lastLanguage;
          this.ngZone.run(() => callbacks.onSegmentForm!(parsed));
        } else if (parsed.type === 'stream_done' && callbacks.onStreamDone) {
          this.ngZone.run(() => callbacks.onStreamDone!(parsed));
        } else if (parsed.type === 'text' && callbacks.onWord) {
          this.ngZone.run(() => callbacks.onWord(parsed.word));
        }
      } catch (e) {
        console.warn('[AgentService] Parse error:', data.slice(0, 80), e);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      lines.forEach(l => processLine(l.trim()));
    }

    this.ngZone.run(() => callbacks.onDone());

  } catch (error) {
    console.error('[AgentService] Stream error:', error);
    this.ngZone.run(() => {
      if (callbacks.onError) callbacks.onError(error);
      else callbacks.onDone();
    });
  }
}
 async sendTourForm(
    formData: {
      city:           string;
      departure_date?: string;
    },
    callbacks: StreamCallbacks,
    language?: string
    
  ): Promise<void> {
    const tenantId  = this.getTenantId();
    const sessionId = this.getSessionId();
    const userId    = this.getUserId();
 
    const entities: { type: string; value: string }[] = [
      { type: 'LOC', value: formData.city },
    ];
    if (formData.departure_date) {
      entities.push({ type: 'DATE', value: formData.departure_date });
    }
 
    const token = await this.getToken();
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
 
    const payload = {
      text: '',
      entities,
      classification: [{
        sub_category: 'Tour/Excursion Booking',
        category:     'Activity',
        score:        1.0,
        confidence:   'validated',
      }],
      language: language || this.getLastLanguage(),
      user_id:    userId,
      tenant_id:  tenantId,
      session_id: sessionId,
    };
 
    try {
      const response = await fetch(this.streamUrl, {
        method: 'POST',
        headers,
        body:   JSON.stringify(payload),
      });
 
      const reader  = response.body!.getReader();
      const decoder = new TextDecoder();
      let   buffer  = '';
 
      const processLine = (line: string) => {
        if (!line.startsWith('data: ')) return;
        const data = line.slice(6).trim();
        if (!data || data === '[DONE]') {
          if (data === '[DONE]') this.ngZone.run(() => callbacks.onDone());
          return;
        }
        try {
          const parsed = JSON.parse(data);
          if (parsed.type === 'segment_done' && callbacks.onSegmentDone)
            this.ngZone.run(() => callbacks.onSegmentDone!(parsed));
          else if (parsed.type === 'segment_form' && callbacks.onSegmentForm)
            this.ngZone.run(() => callbacks.onSegmentForm!(parsed));
          else if (parsed.type === 'stream_done' && callbacks.onStreamDone)
            this.ngZone.run(() => callbacks.onStreamDone!(parsed));
          else if (parsed.type === 'text' && callbacks.onWord)
            this.ngZone.run(() => callbacks.onWord(parsed.word));
          else if (parsed.type === 'status' && callbacks.onStatus)
            this.ngZone.run(() => callbacks.onStatus!(parsed.step, parsed.message ?? '', parsed.sub_category));
          else if (parsed.type === 'json' && callbacks.onJson)
            this.ngZone.run(() => callbacks.onJson(parsed.data));
        } catch (e) {
          console.warn('[AgentService] sendTourForm parse error:', data.slice(0, 80), e);
        }
      };
 
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        lines.forEach(l => processLine(l.trim()));
      }
 
      if (buffer.trim()) buffer.split('\n').forEach(l => processLine(l.trim()));
      this.ngZone.run(() => callbacks.onDone());
 
    } catch (error) {
      console.error('[AgentService] sendTourForm error:', error);
      this.ngZone.run(() => {
        if (callbacks.onError) callbacks.onError(error);
        else callbacks.onDone();
      });
    }
  }
 
  sendTransportForm(formData: {
    origin: string; destination: string; departure_date: string;
    return_date?: string; adults: number; cabin_class: string;
    price_min?: number; price_max?: number;
  }): Observable<AgentMessageResponse[]> {
    const tenantId  = this.getTenantId();
    const sessionId = this.getSessionId();
    const userId    = this.getUserId();

    const entities: { type: string; value: string }[] = [
      { type: 'LOC',   value: formData.origin },
      { type: 'LOC',   value: formData.destination },
      { type: 'DATE',  value: formData.departure_date },
      { type: 'PRICE', value: String(formData.adults) },
      { type: 'MISC',  value: formData.cabin_class },
    ];
    if (formData.return_date) entities.push({ type: 'DATE',  value: formData.return_date });
    if (formData.price_min)   entities.push({ type: 'PRICE', value: String(formData.price_min) });
    if (formData.price_max)   entities.push({ type: 'PRICE', value: String(formData.price_max) });

    return this.http.post<AgentMessageResponse[]>(this.baseUrl, {
      text: '', entities,
      classification: [{ sub_category: 'Flight Booking', category: 'Reservation', score: 1.0, confidence: 'validated' }],
      language: this.getLastLanguage(),
      user_id: userId, tenant_id: tenantId, session_id: sessionId
    });
  }

  getCurrentContext(): { tenant_id: string; user_id: string; session_id: string } {
    return { tenant_id: this.getTenantId(), user_id: this.getUserId(), session_id: this.getSessionId() };
  }

  private async getHeaders(): Promise<HttpHeaders> {
    const token = await this.getToken();
    let headers = new HttpHeaders();
    if (token) headers = headers.set('Authorization', `Bearer ${token}`);
    return headers;
  }

  async getConversationHistory(sessionId: string, tenantId: string): Promise<any> {
    const url     = `${this.apiUrl}chat/history/${encodeURIComponent(sessionId)}?tenant_id=${tenantId}`;
    const headers = await this.getHeaders();
    try {
      return await this.http.get(url, { headers }).toPromise();
    } catch (error) {
      console.error('Erreur chargement historique:', error);
      return null;
    }
  }
}