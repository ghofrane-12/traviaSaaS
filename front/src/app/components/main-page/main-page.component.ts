// main-page.component.ts
import { Component, computed, signal, OnInit, OnDestroy, HostListener, ViewChild, ChangeDetectorRef } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, ActivatedRoute } from '@angular/router';
import { InputComponent } from '../chat-input/chat-input.component';
import { ChatWindowComponent } from '../chat-window/chat-window.component';
import { SidebarComponent } from '../sidebar/sidebar.component';
import { StaySearchComponent } from '../stay-search/stay-search.component';
import { FlightSearchComponent } from '../flight-search/flight-search.component';
import { AgentService, AgentMessageResponse, AgentForm, SegmentDoneEvent, SegmentFormEvent, StreamDoneEvent } from '../../services/agent.service';
import { UserService } from '../../services/user.service';
import { ResultService, SearchResults, SegmentResult, SegmentForm } from '../../services/result.service';
import { Subscription } from 'rxjs';
import { MixedSearchComponent } from '../mixed-search/mixed-search.component';
import { TourSearchComponent } from '../tour-search/tour-search.component';
import { Auth } from '@angular/fire/auth';
import { ReviewFormComponent } from '../review-form/review-form.component';
import { ResultsCacheService } from '../../services/results-cache.service';

export interface Message {
  text:            string;
  sender:          'user' | 'bot';
  timestamp?:      Date;
  category?:       string;
  deberta_category?: string;
  sub_category?:   string;
  language?:       string;
  score?:          number;
  entities?:       { entity: string; value: string }[];
  suggestions?:    any[];
  isStreaming?:    boolean;
  json?:           any;
  _meta?: { status?: string; reason?: string };
  clarification?: {
    subcats:  { id: number; label: string; confidence?: number }[];
    segment:  string;
  };
  activeForm?: 'review' | 'ticket' | null;
formSuccess?: boolean;
sections?:   { title: string; content: string; items?: string[] }[];
actions?:    { label: string; action: string }[];
}

export interface StreamingStep {
  id:      string;
  message: string;
  state:   'pending' | 'done' | 'error';
  count?:  number;
  type?:   string;
}

@Component({
  selector: 'app-main-page',
  standalone: true,
  imports: [
    InputComponent,
    ChatWindowComponent,
    SidebarComponent,
    StaySearchComponent,
    FlightSearchComponent,
    CommonModule,
    MixedSearchComponent,
    TourSearchComponent,
    ReviewFormComponent
  ],
  templateUrl: './main-page.component.html',
  styleUrls: ['./main-page.component.scss']
})
export class MainPageComponent implements OnInit, OnDestroy {
  @ViewChild(ChatWindowComponent) chatWindow!: ChatWindowComponent;
userMenuOpen = false;
showReviewForm = false;

  collapsed = false;
  isMobile  = false;
  mobileSidebarOpen = false;

  private currentStreamingMessage: any = null;
  private accumulatedResponse:     any = null;
  public  isStreaming = false;
  private currentSessionId: string = '';
  public currentTenantId:  string = '';
  private historyLoaded = false;
  private ambiguousQueue = signal<any[]>([]);
  

  private routeSubscription:   Subscription | null = null;
  private resultsSubscription: Subscription | null = null;

  public streamingSteps = signal<StreamingStep[]>([]);


  private _navigatedViaSegments = false;

  showStayForm         = signal(false);
  stayFormData         = signal<AgentForm | null>(null);
  stayEntities         = signal<{ type: string; value: string | null }[]>([]);
  stayOriginalLanguage = signal<string>('fr');

  showTransportForm         = signal(false);
  transportFormData         = signal<AgentForm | null>(null);
  transportEntities         = signal<{ type: string; value: string | null }[]>([]);
  transportOriginalLanguage = signal<string>('fr');
  showMixedForm         = signal(false);
  mixedFormData         = signal<any>(null);
  mixedFormEntities     = signal<{ type: string; value: string | null }[]>([]);
  mixedFormLanguage     = signal<string>('fr');
  showChatInput = signal(true);

  clarificationPending  = signal(false);
  clarificationSubcats  = signal<{ id: number; label: string; confidence?: number }[]>([]);
  clarificationSegment  = signal('');
  clarificationAccepted = signal<any[]>([]);
  clarificationEntities = signal<any>({});
  showTourForm  = signal(false);
  tourFormData  = signal<SegmentForm | null>(null);
  tourEntities         = signal<{ type: string; value: string | null }[]>([]);  
  tourOriginalLanguage = signal<string>('fr');   
  private messagesSignal = signal<Message[]>([
    { text: "👋 Bonjour ! Je suis votre assistant voyage. Où souhaitez-vous partir en aventure aujourd'hui ?", sender: 'bot', timestamp: new Date() }
  ]);

  get messages(): Message[] { return this.messagesSignal(); }

  agencyName = computed(() => this.userService.agencyName());
  agencyLogo = computed(() => this.userService.agencyLogo() || undefined);
  fullName   = computed(() => this.userService.fullName());
  role       = computed(() => this.userService.role());
  isAdmin    = computed(() => this.userService.isAdmin());

  constructor(
    public  userService:  UserService,
    public agentService: AgentService,
    private router:       Router,
    private route:        ActivatedRoute,
    private resultService: ResultService,
    private cdr:          ChangeDetectorRef,
    private auth:          Auth,
    private resultsCache:  ResultsCacheService
  ) {}

ngOnInit(): void {
  this.checkScreenSize();
  window.addEventListener('resize', this.onResize.bind(this));

  const user = this.userService.session();
  this.currentTenantId = user?.tenant_id || this.agentService.getTenantId();

  if (this.resultService.getSegmentsCount() === 0 &&
      this.resultService.getPendingFormsCount() === 0) {
    this.resultService.clearSegments();
  }

  const unsubscribeAuth = this.auth.onAuthStateChanged((firebaseUser) => {
    unsubscribeAuth(); 

   this.routeSubscription = this.route.queryParams.subscribe(params => {
  if (params['session_id']) {
    this.loadSpecificConversation(params['session_id']);
  } else if (params['new'] === '1') {
    this.resetToNewConversation(true);
  } else {
    this.resetToNewConversation(false);
  }
});
  });
}
goToResultsPage(data: any) {
  if (this.router.url.startsWith('/results')) return;

  console.log('🔍 Navigation vers results avec:', data);
  this.showStayForm.set(false);
  this.showTransportForm.set(false);
  this.stayFormData.set(null);
  this.transportFormData.set(null);
  this.showChatInput.set(true);

  if (data.type === 'hotel_search' || data.type === 'flight_search') {
    this.resultService.clearPendingForms();
    this.router.navigate(['/results'], {
      queryParams: { streaming: '1' },
      state: { pendingFormData: data },
    });
  }
}
toggleUserMenu(): void {
  this.userMenuOpen = !this.userMenuOpen;
}

getAdmin(): boolean {
  const r = this.role()?.toLowerCase();
  return r === 'admin' || r === 'super_admin';
}
navigateTo(path: string): void {
  this.userMenuOpen = false;
  this.router.navigate([path]);
}

openReviewForm(): void {
  this.userMenuOpen = false;
  this.showReviewForm = true;
}

  ngOnDestroy(): void {
    window.removeEventListener('resize', this.onResize.bind(this));
    this.routeSubscription?.unsubscribe();
    this.resultsSubscription?.unsubscribe();
  }

  // =========================================================================
  // STEPS helpers
  // =========================================================================

  private _addStep(id: string, message: string, subType?: string): void {
    this.streamingSteps.update(steps => {
      if (steps.find(s => s.id === id)) return steps;
      return [...steps, { id, message, state: 'pending', type: subType }];
    });
  }

private _doneStep(id: string, count?: number): void {
  const alt = id.includes('_') ? id.replace(/_/g, ' ') : id.replace(/ /g, '_');
  this.streamingSteps.update(steps =>
    steps.map(s =>
      (s.id === id || s.id === alt)
        ? { ...s, state: 'done' as const, ...(count !== undefined ? { count } : {}) }
        : s
    )
  );
}

  private _errorStep(id: string): void {
    this.streamingSteps.update(steps =>
      steps.map(s => s.id === id ? { ...s, state: 'error' as const } : s)
    );
  }

  private _subTypeFromCategory(subCategory: string): string {
    const lower = subCategory.toLowerCase();
    if (lower.includes('flight') || lower.includes('vol'))        return 'flight';
    if (lower.includes('hotel') || lower.includes('stay'))        return 'hotel';
    if (lower.includes('restaurant') || lower.includes('gastro')) return 'restaurant';
    if (lower.includes('transport') || lower.includes('car'))     return 'transport';
    if (lower.includes('activity') || lower.includes('tour') || lower.includes('event')) return 'activity';
    if (lower.includes('specialty') || lower.includes('food'))    return 'specialty';
    return 'info';
  }

  private _subTypeFromResultType(resultType: string): string {
    const map: Record<string, string> = {
      'flight_search_results':     'flight',
      'hotel_search_results':      'hotel',
      'restaurant_search_results': 'restaurant',
      'transport_search_results':  'transport',
      'activity_search_results':   'activity',
      'specialty_search_results':  'specialty',
      'info_results':              'info',
      'human_required':            'info',
      'error':                     'error',
    };
    return map[resultType] || 'info';
  }

  // =========================================================================
  // STREAM CALLBACKS
  // =========================================================================

  private _buildStreamCallbacks() {
    return {
      onWord: (word: string) => {
        if (this.currentStreamingMessage) {
          this.currentStreamingMessage.text += word + ' ';
          setTimeout(() => this.chatWindow?.forceScrollToBottom(), 20);
        }
      },

      onStatus: (step: string, message: string, subCategory?: string) => {
        if (step === 'classifier') {
          this.streamingSteps.set([]);
          this._addStep('classifier', message || 'Analyse de votre demande...');
        } else if (step === 'classifier_done') {
          this._doneStep('classifier');
        } else if (step === 'agent_start' && subCategory) {
          const subType = this._subTypeFromCategory(subCategory);
          this._addStep(subCategory, message, subType);
        } else if (step === 'agent_start' && !subCategory) {
          this._addStep('agent_start_' + Date.now(), message);
        }
        this.resultService.setStreamingSteps(this.streamingSteps());
        setTimeout(() => this.chatWindow?.forceScrollToBottom(), 20);
      },

      onSegmentDone: (event: SegmentDoneEvent) => {
          console.log('[SegmentDone] event.offers:', event.offers?.length, 
              '| event.results:', event.results?.length,
              '| event.results[0]?.currency:', event.results?.[0]?.currency,
              '| event.offers?.[0]?.currency:', event.offers?.[0]?.currency);
  if ((event.result_type as string) === 'management_question') {
    if (this.currentStreamingMessage) {
      this.currentStreamingMessage.text        = event.message;
      this.currentStreamingMessage.isStreaming = false;
      this.currentStreamingMessage = null;
    } else {
      this.messagesSignal.update(msgs => [
        ...msgs.filter(m => !m.isStreaming),
        {
          text:      event.message,
          sender:    'bot' as const,
          timestamp: new Date(),
        }
      ]);
    }
    this.cdr.detectChanges();
    return;
  }

  if (this.currentStreamingMessage) {
    if ((event as any).sections?.length) {
      this.currentStreamingMessage.sections = (event as any).sections;
    }
    if ((event as any).actions?.length) {
      this.currentStreamingMessage.actions = (event as any).actions;
    }
  }
                this.resultService.clearPendingClarification();

        const { seg_id, result_type, message, follow_up,
                sections, actions, results, query,
                suggestions, label } = event;

        const count = results?.length ?? 0;
        this._doneStep(seg_id, count > 0 ? count : undefined);
        this.resultService.setStreamingSteps(this.streamingSteps());

          let segResults = event.results;
  
  if ( event.offers?.length) {
    segResults = event.offers;
  }

  const segment: SegmentResult = {
    seg_id:      event.seg_id,
    result_type: event.result_type,
    message:     event.message     || '',
    follow_up:   event.follow_up   || '',
    suggestions: event.suggestions || [],
    show_ticket_form: event.show_ticket_form || false,
    ...(event.sections ? { sections: event.sections } : {}),
    ...(event.actions  ? { actions:  event.actions  } : {}),
    ...(segResults     ? { results:  segResults     } : {}),  // ← offers EUR
    ...(event.query    ? { query:    event.query    } : {}),
    ...(event.label    ? { label:    event.label    } : {}),
  };

        this.resultService.addSegment(segment);

          const subType = this._subTypeFromResultType(result_type);
          const isError = result_type === 'error';
        
  if (!this._navigatedViaSegments && !isError) {
          this._navigatedViaSegments = true;
   if (!this.router.url.startsWith('/results')) {
      this.router.navigate(['/results'], { queryParams: { streaming: '1' } });
    }        }

        setTimeout(() => this.chatWindow?.forceScrollToBottom(), 20);
      },
    onSegmentForm: (event: SegmentFormEvent) => {
        const form: SegmentForm = {
          seg_id:           event.seg_id,
          sub_category:     event.sub_category,
          sub_categories:   (event as any).sub_categories || [],
          is_merged:        (event as any).is_merged || false,
          message:          event.message,
          form:             event.form,
          steps:            event.steps            || [],
          entities:         event.entities         || [],
          pending_segments: (event as any).pending_segments || {},
        };
 
        this._doneStep(event.seg_id);
        this.resultService.setStreamingSteps(this.streamingSteps());
 
        if (form.is_merged && form.sub_category === 'Mixed Booking') {
          const language = this.agentService.getLastSegmentFormLanguage();
          this.mixedFormData.set(form);
          this.mixedFormEntities.set(form.entities || []);
          this.mixedFormLanguage.set(language);
          this.showMixedForm.set(true);
          this.showStayForm.set(false);
          this.showTransportForm.set(false);
 
          this.resultService.addPendingForm(form);
 
          if (!this._navigatedViaSegments) {
            this._navigatedViaSegments = true;
            this.router.navigate(['/results'], { queryParams: { streaming: '1' } });
          }
          return;
        }
            const sub = form.sub_category?.toLowerCase() || '';
            const isTour = sub.includes('tour') || sub.includes('excursion');
            if (isTour) {
              const language = this.agentService.getLastSegmentFormLanguage();
              this.tourFormData.set(form);
              this.tourEntities.set(form.entities || []);
              this.tourOriginalLanguage.set(language);
              this.showTourForm.set(true);
              this.showStayForm.set(false);
              this.showTransportForm.set(false);

              this.resultService.addPendingForm(form);

              if (!this._navigatedViaSegments) {
                this._navigatedViaSegments = true;
                this.router.navigate(['/results'], { queryParams: { streaming: '1' } });
              }
              return;
            }
        this.resultService.addPendingForm(form);
 
        if (!this._navigatedViaSegments) {
          this._navigatedViaSegments = true;
          this.router.navigate(['/results'], { queryParams: { streaming: '1' } });
        }
      },

      onStreamDone: (event: StreamDoneEvent) => {
        if (event.loyalty_summary) {
          this.resultService.patchLoyalty(event.loyalty_summary);
        }
        if ((event as any).suggestions?.length) {
          this.resultService.patchSuggestions((event as any).suggestions);
        } 
        if ((event as any).cross_sell_proposals?.length) {
          this.resultService.setCrossSellProposals((event as any).cross_sell_proposals);
        }

        this.resultService.setStreamingComplete();
         setTimeout(async () => {
    const sessionId = this.currentSessionId;
    const segments  = this.resultService.getSegments();
    console.log('[Cache] 💾 Sauvegarde | sessionId:', sessionId, '| segments:', segments.length);
    if (sessionId && segments.length > 0) {
      await this.resultsCache.saveResults(sessionId, segments);
      console.log('[Cache] ✅ Sauvegarde effectuée');
    }
  }, 500);
        console.log(
          `[MainPage] stream_done | total=${event.total_segments} | ` +
          `tier=${event.loyalty_summary?.tier}`
        );
        
      },

onJson: (jsonData: any) => {
  this.accumulatedResponse = jsonData;

  if (this.currentStreamingMessage) {
    this.currentStreamingMessage.json         = jsonData;
    this.currentStreamingMessage.category     = jsonData.category;
    this.currentStreamingMessage.sub_category = jsonData.sub_category;
    this.currentStreamingMessage.language     = jsonData.language;
    this.currentStreamingMessage.entities     = jsonData.entities;
    this.currentStreamingMessage.suggestions  = jsonData.suggestions;
  }
  if (jsonData?.language) {
        this.agentService.setLastLanguage(jsonData.language);
    }
  if (jsonData?.pending_segments) {
    this.resultService.setPendingSegmentsFromBackend(jsonData.pending_segments);
  }

  if (jsonData?.status === 'clarification_needed') {
    const subcats = (jsonData?.subcats || []).map((s: any) => ({
      ...s,
      display_phrase: s.display_phrase || s.label,
    }));
    const question = jsonData?.message || jsonData?.reply || '';

    this.clarificationPending.set(true);
    this.clarificationSubcats.set(subcats);
    this.clarificationSegment.set(jsonData?.segment || '');
    this.clarificationAccepted.set(this.accumulatedResponse?.classification || []);
    this.ambiguousQueue.set(jsonData?._meta?.ambiguous_queue || []); 

    if (this.currentStreamingMessage) {
      this.currentStreamingMessage.text          = question;
      this.currentStreamingMessage.isStreaming   = false;
      this.currentStreamingMessage.clarification = {
        subcats,
        segment: jsonData?.segment || '',
      };
    }

    this.resultService.setPendingClarification({
      question,
      subcats,
      segment: jsonData?.segment || '',
    });
    return;
  }

if (['rejected', 'blocked', 'error', 'api_error'].includes(jsonData?.status)) {
  const errorText = jsonData?.message || jsonData?.reply
    || '❌ Une erreur est survenue. Veuillez réessayer.';

  if (this.currentStreamingMessage) {
    this.currentStreamingMessage.text        = errorText;
    this.currentStreamingMessage.isStreaming  = false;
    this.currentStreamingMessage._meta        = {
      status: jsonData.status,
      reason: jsonData?.reason || '',
    };
    this.currentStreamingMessage = null;
  } else {
    this.messagesSignal.update(msgs => [
      ...msgs.filter(m => !m.isStreaming),
      {
        text:      errorText,
        sender:    'bot',
        timestamp: new Date(),
        _meta:     { status: jsonData.status, reason: jsonData?.reason || '' },
      }
    ]);
  }

  this.isStreaming            = false;
  this._navigatedViaSegments  = false;
  this._resetClarification();
  this.streamingSteps.set([]);
  this.resultService.setStreamingSteps([]);
  setTimeout(() => this.chatWindow?.forceScrollToBottom(), 50);
  return;
}
  this._resetClarification();

  if (jsonData?.pending_clarification) {
    const pc = jsonData.pending_clarification;
    const subcats = (pc.subcats || []).map((s: any) => ({
      ...s,
      display_phrase: s.display_phrase || s.label,
    }));

    this.clarificationPending.set(true);
    this.clarificationSubcats.set(subcats);
    this.clarificationSegment.set(pc.segment || '');
    this.clarificationAccepted.set(this.accumulatedResponse?.classification || []);

    const clarifMessage: Message = {
      text:      pc.question || '',
      sender:    'bot',
      timestamp: new Date(),
      clarification: { subcats, segment: pc.segment || '' },
    };
    this.messagesSignal.update(msgs => [...msgs, clarifMessage]);

    this.resultService.setPendingClarification(pc);
    return;
  }

  if (jsonData?.loyalty_summary && this.resultService.getSegmentsCount() > 0) {
    this.resultService.patchLoyalty(jsonData.loyalty_summary);
  }
},

onDone: () => {
  if (this.currentStreamingMessage) {
    this.currentStreamingMessage.isStreaming = false;
    this.currentStreamingMessage = null;
  }
  this.isStreaming = false;
  setTimeout(() => {
    this.streamingSteps.set([]);
    this.resultService.setStreamingSteps([]);
  }, 1500);
  if (this.accumulatedResponse?.status === 'waiting_for_form') {
    this._navigatedViaSegments = false;
    return;
  }

  this.resultService.setStreamingComplete();



  if (!this._navigatedViaSegments && this.accumulatedResponse) {
    this.processAccumulatedResponse(this.accumulatedResponse);
  }

  this._navigatedViaSegments = false;
  setTimeout(() => this.chatWindow?.forceScrollToBottom(), 50);
},

onError: (error: any) => {
  console.error('Stream error:', error);
  if (this.currentStreamingMessage) {
    this.currentStreamingMessage.text = 'Erreur de connexion. Veuillez réessayer.';
    this.currentStreamingMessage.isStreaming = false;
    this.currentStreamingMessage = null;
  }
  this.isStreaming = false;

  this.streamingSteps().forEach(s => {
    if (s.state === 'pending') this._errorStep(s.id);
  });

  setTimeout(() => {
    this.streamingSteps.set([]);
    this.resultService.setStreamingSteps([]);
  }, 1500); 

  this.resultService.clearSegments();
  this._resetClarification();
},
    };
  }

  // =========================================================================
  // ENVOI MESSAGE
  // =========================================================================

  handleSendMessage(text: string): void {
    if (this.isStreaming) return;
    const sessionId = this.agentService.getSessionId();
    if (sessionId) {
      this.resultsCache.clearCache(sessionId);
    }

      console.log('🔍 [handleSendMessage] Reset _navigatedViaSegments = false');
    this.agentService.syncSessionIdFromComponent(this.currentSessionId);
    this.resultService.clearSegments();
    this.resultService.clearPendingForms();
    this._navigatedViaSegments = false;

    const isClarification = this.clarificationPending();
    const subcats  = this.clarificationSubcats();
    const segment  = this.clarificationSegment();
    this._resetClarification();

    const userMessage: Message = { text, sender: 'user', timestamp: new Date() };
    this.messagesSignal.update(msgs => [...msgs, userMessage]);

    this.currentStreamingMessage = { text: '', sender: 'bot', isStreaming: true, timestamp: new Date() };
    this.messagesSignal.update(msgs => [...msgs, this.currentStreamingMessage]);
    this.accumulatedResponse = null;
    this.isStreaming = true;

    setTimeout(() => this.chatWindow?.forceScrollToBottom(), 50);

    const clarificationContext = isClarification
      ? {
          clarification_pending:  true,
          clarification_subcats:  subcats,
          clarification_segment:  segment,
          clarification_accepted: this.clarificationAccepted(),
          clarification_entities: this.clarificationEntities(),
          ambiguous_queue:        this.ambiguousQueue(),   

        }
      : undefined;

    this.agentService.sendMessageStream(text, this._buildStreamCallbacks(), clarificationContext);
  }

onClarificationChoice(subcat: { id: number; label: string; display_phrase?: string }): void {
  if (this.isStreaming) return;
  this.resultService.clearPendingClarification();

  const segment = this.clarificationSegment();
  const subcats = this.clarificationSubcats();

  const displayText = subcat.display_phrase || subcat.label;

  const userMessage: Message = {
    text:      displayText,
    sender:    'user',
    timestamp: new Date(),
  };
  this.messagesSignal.update(msgs => [
    ...msgs.map(m => m.clarification
      ? { ...m, clarification: undefined }
      : m
    ),
    userMessage,
  ]);

  this.currentStreamingMessage = {
    text: '', sender: 'bot', isStreaming: true, timestamp: new Date()
  };
  this.messagesSignal.update(msgs => [...msgs, this.currentStreamingMessage]);
  this.accumulatedResponse = null;
  this.isStreaming = true;

  this._resetClarification();
  this._navigatedViaSegments = false;

  setTimeout(() => this.chatWindow?.forceScrollToBottom(), 50);

  this.agentService.sendMessageStream(
    subcat.label,  
    this._buildStreamCallbacks(),
    {
      clarification_pending:  true,
      clarification_subcats:  subcats,
      clarification_segment:  segment,
      clarification_accepted: this.clarificationAccepted(),
      clarification_entities: this.clarificationEntities(),
      clarification_choice:   { id: subcat.id, label: subcat.label },
      ambiguous_queue:        this.ambiguousQueue(),
    }
  );
}

  // =========================================================================
  // FORMULAIRES
  // =========================================================================

  private _handlePendingForm(pendingForm: any): void {
    if (this._navigatedViaSegments) return;
    if (!pendingForm?.sub_category) return;
    if (this.router.url.startsWith('/results')) return;

    const sub      = pendingForm.sub_category.toLowerCase();
    const isHotel  = sub.includes('hotel') || sub.includes('stay') || sub.includes('hébergement');
    const isFlight = sub.includes('flight') || sub.includes('vol')
                  || sub.includes('avion')  || sub.includes('transport');
    const isTour   = sub.includes('tour') || sub.includes('excursion');  

    const formData = pendingForm.form     ?? null;
    const entities = pendingForm.entities ?? [];
    const language = pendingForm.language ?? this.agentService.getLastLanguage()?? 'query_fr';

    if (isHotel) {
      this.stayFormData.set(formData);
      this.stayEntities.set(entities);
      this.stayOriginalLanguage.set(language);
      this.showStayForm.set(true);
      this.showTransportForm.set(false);
    } else if (isFlight) {
      this.transportFormData.set(formData);
      this.transportEntities.set(entities);
      this.transportOriginalLanguage.set(language);
      this.showTransportForm.set(true);
      this.showStayForm.set(false);
    } else if (isTour) {
      this.tourFormData.set(formData);
      this.tourEntities.set(entities);
      this.tourOriginalLanguage.set(language);
      this.showTourForm.set(true);
      this.showStayForm.set(false);
      this.showTransportForm.set(false);
    }

    if (pendingForm.message && this.currentStreamingMessage === null) {
      this.messagesSignal.update(msgs => [...msgs, {
        text:      pendingForm.message,
        sender:    'bot',
        timestamp: new Date(),
        _meta:     { status: 'need_more_info', reason: pendingForm.sub_category }
      }]);
    }
  }

  onStayFormSubmit(formResult: any): void {
    this.showStayForm.set(false);
    this.showChatInput.set(true);
    this.stayFormData.set(null);
    this.stayEntities.set([]);

    if (formResult.error) {
      this.messagesSignal.update(msgs => [...msgs, { text: `❌ ${formResult.message}`, sender: 'bot', timestamp: new Date() }]);
      return;
    }

    const hasResults = (formResult.results?.length > 0) || (formResult.offers?.length > 0);
    if (!hasResults) {
      if (formResult.reply) {
        this.messagesSignal.update(msgs => [...msgs, { text: formResult.reply, sender: 'bot', timestamp: new Date() }]);
      }
      return;
    }

    const rawList = formResult.results || formResult.offers;
    const fv      = formResult._formValues || {};

    const searchResults: SearchResults = {
      type: 'hotel_search_results',
      query: { city: fv.city || '', check_in: fv.check_in || '', check_out: fv.check_out || '', guests: fv.guests || 1, rooms: fv.rooms || 1 },
      results: rawList.map((hotel: any, i: number) => ({
        id: hotel.id || `hotel_${i}`, type: 'hotel',
        name: hotel.name || hotel.hotel_name || 'Hôtel',
        location: { city: hotel.location?.city || hotel.city || fv.city, country: hotel.location?.country || '', address: hotel.location?.address || '' },
        rating: hotel.rating || hotel.review_score || 0,
        rating_count: hotel.review_count || 0,
        price_per_night: hotel.price_per_night || 0, total_price: 0,
        currency: hotel.currency || 'EUR',
        images: hotel.photoUrls || hotel.images || [],
        amenities: hotel.amenities || [], offers: hotel.offers || [],
        best_offer: hotel.best_offer || null, stars: hotel.stars || 0,
        nights: hotel.nights || 0, token: hotel.token || '',
        actions: [{ label: 'Voir détails', action: 'details', url: `/hotel/${hotel.id || i}` }],
      })),
      suggestions:     formResult.suggestions     || [],
      loyalty_summary: formResult.loyalty_summary || {},
      message:         formResult.reply           || '',
      follow_up:       formResult.follow_up        || '',
    };

    this.resultService.setResults(searchResults);
    this.router.navigate(['/results']);
  }

  onTransportFormSubmit(formResult: any): void {
    this.showTransportForm.set(false);
    this.showChatInput.set(true);
    this.transportFormData.set(null);
    this.transportEntities.set([]);

    if (formResult.error) {
      this.messagesSignal.update(msgs => [...msgs, { text: `❌ ${formResult.message}`, sender: 'bot', timestamp: new Date() }]);
      return;
    }

    const hasResults = (formResult.results?.length > 0) || (formResult.offers?.length > 0);
    if (!hasResults) {
      if (formResult.reply) {
        this.messagesSignal.update(msgs => [...msgs, { text: formResult.reply, sender: 'bot', timestamp: new Date() }]);
      }
      return;
    }

    const rawList = formResult.results || formResult.offers;
    const fv      = formResult._formValues || {};

    const searchResults: SearchResults = {
      type: 'flight_search_results',
      query: { origin: fv.origin || '', destination: fv.destination || '', departure_date: fv.departure_date || '', return_date: fv.return_date || null, adults: fv.adults || 1 },
      results: rawList.map((flight: any, i: number) => ({
        id: flight.id || `flight_${i}`, type: 'flight',
        airline: flight.airline || 'Compagnie',
        flight_number: flight.flight_number || `FL${i}`,
        stops: flight.stops ?? 0, stop_details: flight.stop_details || null,
        duration: flight.duration || 'N/A',
        departure: { airport: flight.departure?.airport || fv.origin, city: flight.departure?.city || fv.origin, time: flight.departure?.time || 'N/A', date: fv.departure_date },
        arrival:   { airport: flight.arrival?.airport   || fv.destination, city: flight.arrival?.city   || fv.destination, time: flight.arrival?.time   || 'N/A', date: fv.departure_date },
        amenities: flight.amenities || [],
        fare_options: flight.fare_options || [{ name: 'Standard', price: flight.price || 0, currency: 'EUR', features: [], selected: false, recommended: true }],
      })),
      suggestions:     formResult.suggestions     || [],
      loyalty_summary: formResult.loyalty_summary || {},
      message:         formResult.reply           || '',
      follow_up:       formResult.follow_up        || '',
    };

    this.resultService.setResults(searchResults);
    this.router.navigate(['/results']);
  }

  onStayFormCancel(): void {
    this.showStayForm.set(false);
    this.showChatInput.set(true);
    this.stayFormData.set(null);
    this.stayEntities.set([]);
  }
  onTourFormCancel(): void {
  this.showTourForm.set(false);
  this.showChatInput.set(true);
  this.tourFormData.set(null);
  this.tourEntities.set([]);
}

onTourFormSubmit(formResult?: any): void {
  this.showTourForm.set(false);
  this.showChatInput.set(true);
  this.tourFormData.set(null);
  this.tourEntities.set([]);
}

onTourFormNavigate(data: any): void {
  this.showTourForm.set(false);
  this.tourFormData.set(null);
  this.showChatInput.set(true);

  this.agentService.sendTourForm(
    data.formValues,
    this._buildStreamCallbacks(),
    data.originalLanguage || this.agentService.getLastLanguage(),
  );
}
  onTransportFormCancel(): void {
    this.showTransportForm.set(false);
    this.showChatInput.set(true);
    this.transportFormData.set(null);
    this.transportEntities.set([]);
  }
onMixedFormNavigate(data: any): void {
    this.showMixedForm.set(false);
    this.mixedFormData.set(null);
    this.showChatInput.set(true);
 
    const pendingSegments = this.resultService.getPendingSegmentsFromBackend();
 
    if (data.type === 'mixed_search') {
      this.resultService.clearPendingForms();
      this.router.navigate(['/results'], {
        queryParams: { streaming: '1' },
        state: {
          pendingFormData: {
            ...data,
            type: 'mixed_search',
          },
          pendingSegments,
        },
      });
    }
  }
 
  onMixedFormCancel(): void {
    this.showMixedForm.set(false);
    this.showChatInput.set(true);
    this.mixedFormData.set(null);
    this.mixedFormEntities.set([]);
    this.resultService.clearPendingForms();
  }
 
  // =========================================================================
  // PROCESSACCUMULATED (fallback ancien backend)
  // =========================================================================
hasResults(): boolean {
  return this.resultService.getSegmentsCount() > 0;
}
 getRoleLabel(): string {
      const role = this.role()?.toLowerCase();
    if (role === 'super_admin') return 'Super Admin';
    if (role === 'admin') return 'Administrateur';
    if (role === 'visiteur') return 'Visiteur';
    return 'Agent';
  }
goToResults(): void {
  const segments = this.resultService.getSegments();
  this.router.navigate(['/results'], {
    state: { segments, streaming: true }
  });
}
  private processAccumulatedResponse(res: any): void {
    if (!res) return;

    if (res.status === 'waiting_for_form') return;

   const response = res as AgentMessageResponse;

  if (response.status === 'error' || response.status === 'api_error') {
    const errorText = response.reply || response.message
      || '❌ Une erreur est survenue. Veuillez réessayer.';

    this.messagesSignal.update(msgs => [
      ...msgs.filter(m => !m.isStreaming),
      {
        text:      errorText,
        sender:    'bot',
        timestamp: new Date(),
        _meta:     { status: response.status },
      }
    ]);
    this.showStayForm.set(false);
    this.showTransportForm.set(false);
    this.showChatInput.set(true);
    this._resetClarification();
    return;
  }
    const subcat    = response.sub_category?.toLowerCase() || '';
    const isStay    = subcat.includes('hotel') || subcat.includes('stay');
    const isFlight  = subcat.includes('flight') || subcat.includes('vol') || subcat.includes('avion');

    if (response.status === 'need_more_info' && response.form) {
      const originalLanguage = response.language 
          ? response.language 
          : this.agentService.getLastLanguage();
      if (isStay) {
        this.stayFormData.set(response.form);
        this.stayEntities.set(response.entities ?? []);
        this.stayOriginalLanguage.set(originalLanguage);
        this.showStayForm.set(true);
        this.showTransportForm.set(false);
        this.showChatInput.set(false);
      } else if (isFlight) {
        this.transportFormData.set(response.form);
        this.transportEntities.set(response.entities ?? []);
        this.transportOriginalLanguage.set(originalLanguage);
        this.showTransportForm.set(true);
        this.showStayForm.set(false);
        this.showChatInput.set(false);
      }
      return;
    }

    this.showStayForm.set(false);
    this.showTransportForm.set(false);
    this.showChatInput.set(true);

    const rawOffers = (response.info ?? response.offers ?? response.results) as any;
    if (!rawOffers) return;

    if ([
      'flight_search_results', 'hotel_search_results', 'activity_search_results',
      'info_results', 'restaurant_search_results', 'specialty_search_results',
      'transport_search_results',
    ].includes(rawOffers?.type)) {
      this.resultService.setResults(rawOffers);
      this.router.navigate(['/results']);
    }
  }

  // =========================================================================
  // NAVIGATION / HISTORY
  // =========================================================================

private async loadSpecificConversation(sessionId: string): Promise<void> {
  const preserveResults = history.state?.preserveResults ?? false;

  this.messagesSignal.set([]);
  this.isStreaming   = false;
  this.historyLoaded = false;
  if (!preserveResults) {
    this.resultService.clearSegments();
  }

  try {
    const user     = this.userService.session();
    const tenantId = user?.tenant_id || this.agentService.getTenantId();
    const historyData = await this.agentService.getConversationHistory(sessionId, tenantId);

    if (historyData?.history?.length > 0) {
      const loadedMessages: Message[] = [];
      for (const msg of historyData.history) {
        if (msg.user_message?.trim()) {
          loadedMessages.push({ text: msg.user_message, sender: 'user', timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date() });
        }
        if (msg.assistant_response?.reply) {
          loadedMessages.push({ text: msg.assistant_response.reply, sender: 'bot', timestamp: msg.timestamp ? new Date(msg.timestamp) : new Date(), category: msg.assistant_response.category, sub_category: msg.assistant_response.sub_category });
        }
      }
      this.messagesSignal.set(loadedMessages);
      this.currentSessionId = sessionId;
      this.agentService.syncSessionIdFromComponent(this.currentSessionId);
      this.historyLoaded    = true;
      this.cdr.detectChanges();
      setTimeout(() => this.chatWindow?.forceScrollToBottom(), 300);
    } else {
      this.resetToNewConversation();
    }
  } catch (error) {
    console.error('❌ Erreur chargement conversation:', error);
    this.resetToNewConversation();
  }
}

private resetToNewConversation(forceNew: boolean = false): void {
  const user = this.userService.session();
  
  if (user) {
    const storedKey = `session_${user.user_id}_${user.tenant_id}`;
    const existingSession = sessionStorage.getItem(storedKey);
    
    if (existingSession && !forceNew) {
      this.currentSessionId = existingSession;
    } else {
      this.currentSessionId = `${user.user_id}__${user.tenant_id}__${Date.now()}`;
      sessionStorage.setItem(storedKey, this.currentSessionId);
    }
  } else {
    this.currentSessionId = this.agentService.getSessionId();
  }

  this.agentService.syncSessionIdFromComponent(this.currentSessionId);
  this.historyLoaded = false;
  this.isStreaming = false;
  this.showStayForm.set(false);
  this.showTransportForm.set(false);
  this.showChatInput.set(true);

  if (!this.router.url.startsWith('/results')) {
    this.resultService.clearSegments();
  }

  this.messagesSignal.set([{
    text: "👋 Bonjour ! Je suis votre assistant voyage. Où souhaitez-vous partir en aventure aujourd'hui ?",
    sender: 'bot',
    timestamp: new Date()
  }]);
  this.cdr.detectChanges();
}
  private async loadConversationHistory(): Promise<void> {
    if (this.historyLoaded) return;
    try {
      const historyData = await this.agentService.getConversationHistory(this.currentSessionId, this.currentTenantId);
      if (historyData?.history?.length > 0) {
        const loadedMessages: Message[] = [];
        for (const msg of historyData.history) {
          if (msg.user_message?.trim()) loadedMessages.push({ text: msg.user_message, sender: 'user', timestamp: new Date(msg.timestamp) });
          if (msg.assistant_response?.reply) loadedMessages.push({ text: msg.assistant_response.reply, sender: 'bot', timestamp: new Date(msg.timestamp) });
        }
        if (loadedMessages.length > 0) {
          this.messagesSignal.set(loadedMessages);
          this.historyLoaded = true;
          this.cdr.detectChanges();
          setTimeout(() => this.chatWindow?.forceScrollToBottom(), 300);
        }
      }
    } catch (error) {
      console.error('❌ Erreur chargement historique:', error);
    }
  }

  async reloadHistory(): Promise<void> {
    this.historyLoaded = false;
    await this.loadConversationHistory();
  }

  private _resetClarification(): void {
    this.clarificationPending.set(false);
    this.clarificationSubcats.set([]);
    this.clarificationSegment.set('');
    this.clarificationAccepted.set([]);
    this.clarificationEntities.set({});
  }

  // =========================================================================
  // UI helpers
  // =========================================================================

  openMobileSidebar(): void  { if (this.isMobile) { this.mobileSidebarOpen = true;  document.body.style.overflow = 'hidden'; } }
  closeMobileSidebar(): void { if (this.isMobile) { this.mobileSidebarOpen = false; document.body.style.overflow = '';       } }

  private checkScreenSize(): void {
    this.isMobile  = window.innerWidth <= 768;
    if (this.isMobile) this.collapsed = false;
  }

  private onResize(): void { this.checkScreenSize(); }

 @HostListener('document:click', ['$event'])
onDocumentClick(event: Event): void {
  const target = event.target as HTMLElement;
  const isInsideUserMenu = target.closest('.topbar-user');
  
  if (!isInsideUserMenu) {
    this.userMenuOpen = false;
  }

  if (this.isMobile && !this.collapsed) {
    const isSidebar   = target.closest('.sidebar');
    const isToggleBtn = target.closest('.toggle-btn');
    if (!isSidebar && !isToggleBtn) this.collapsed = true;
  }
}
  onSidebarToggle(collapsed: boolean): void { this.collapsed = collapsed; }
}