// results-page/results-page.component.ts
import { Component, OnInit, OnDestroy, ChangeDetectorRef, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, ActivatedRoute } from '@angular/router';
import {
  ResultService, SearchResults, FlightResult, HotelResult,
  ActivityResult, RestaurantResult, SpecialtyResult, Suggestion,
  TransportResult, MultiSegmentResults,
  SegmentResult, SegmentForm, CrossSellProposal
} from '../../services/result.service';
import { FlightCardComponent }     from '../flight-card/flight-card.component';
import { HotelCardComponent }      from '../hotel-card/hotel-card.component';
import { InfoResultsComponent }    from '../info-results/info-results.component';
import { ActivityCardComponent }   from '../activity-card/activity-card.component';
import { RestaurantCardComponent } from '../restaurant-card/restaurant-card.component';
import { SpecialtyCardComponent }  from '../specialty-card/specialty-card.component';
import { TransportCardComponent }  from '../transport-card/transport-card.component';
import { Subscription }            from 'rxjs';
import { ActivityService }         from '../../services/activity.service';
import { AgentService, AgentForm, CrossSellContext } from '../../services/agent.service';
import { UserService }             from '../../services/user.service';
import { StaySearchComponent }     from '../stay-search/stay-search.component';
import { FlightSearchComponent }   from '../flight-search/flight-search.component';
import { MixedSearchComponent } from '../mixed-search/mixed-search.component';
import { TourSearchComponent } from '../tour-search/tour-search.component';
import {TicketFormComponent} from '../ticket-form/ticket-form.component';
import { ResultsCacheService } from '../../services/results-cache.service';

@Component({
  selector:    'app-results-page',
  standalone:  true,
  imports: [
    CommonModule, FlightCardComponent, HotelCardComponent, InfoResultsComponent,
    ActivityCardComponent, RestaurantCardComponent, SpecialtyCardComponent, TransportCardComponent,
    StaySearchComponent, FlightSearchComponent, MixedSearchComponent, TourSearchComponent,TicketFormComponent
  ],
  templateUrl: './results-page.component.html',
  styleUrls:   ['./results-page.component.scss']
})
export class ResultsPageComponent implements OnInit, OnDestroy {
  private _streamDoneTriggered = false;
  ticketFormOpen: Record<string, boolean> = {};

  results: SearchResults | null = null;
  globalSuggestions: Suggestion[] = [];

  segments: SegmentResult[] = [];

  isStreamingMode     = false;
  isLoading           = true;
  isStreamingComplete = false;

  pendingClarification:     any = null;
  isClarificationProcessing = false;

  activeStreamingSteps: any[] = [];

  showStayForm    = false;
  showFlightForm  = false;
  showTourForm    = false;
  stayFormData:   AgentForm | null = null;
  flightFormData: AgentForm | null = null;
  tourFormData:   SegmentForm | null = null;
  formEntities:   any[]  = [];
  formLanguage            = 'fr';
  showMixedForm   = false;
  mixedFormData:  SegmentForm | null = null;
  expandedSuggestionIndex: number | null = null;
  isRestoringCache = true;
  crossSellProposals: CrossSellProposal[] = [];

  private resultsSubscription!:      Subscription;
  private segmentsSubscription!:     Subscription;
  private routeSubscription!:        Subscription;
  private stepsSubscription!:        Subscription;
  private completeSubscription!:     Subscription;
  private pendingFormsSubscription!: Subscription;
  private clarificationSubscription!: Subscription;
  private crossSellSubscription!:    Subscription;

  currentQuery: any = null;

  constructor(
    private router:        Router,
    private route:         ActivatedRoute,
    private activityService: ActivityService,
    private resultService: ResultService,
    public agentService:   AgentService,
    public userService:   UserService,
    private cdr:           ChangeDetectorRef,
    private ngZone:        NgZone,
    private resultsCache: ResultsCacheService,

  ) {}

  ngOnInit(): void {
      this.isLoading = false;

    const navState = this.router.getCurrentNavigation()?.extras?.state
                  ?? (window.history.state || {});
    const pendingFormData = navState['pendingFormData'];

    if (pendingFormData?.type === 'hotel_search' || pendingFormData?.type === 'flight_search' ||
        pendingFormData?.type === 'mixed_search'  || pendingFormData?.type === 'tour_search') {
      this._formHandledViaRouterState = true;
      this.isStreamingMode      = true;
      this.isLoading            = false;
      this.isStreamingComplete  = false;
      this.activeStreamingSteps = [];
      this.resultService.resetStreamingComplete();
      this.resultService.setStreamingSteps([]);
      const pendingSegments = this.resultService.getPendingSegmentsFromBackend();
      this.agentService.sendFormResponse(
        pendingFormData.formValues,
        pendingSegments,
        this._buildFormResponseCallbacks(),
        this.formLanguage,
      );
    }

    this.resultsSubscription = this.resultService.results$.subscribe(results => {
      this.ngZone.run(() => {
        if (results) {
          this.results   = results;
          this.isLoading = false;
          this.cdr.detectChanges();
        }
      });
    });

    this.segmentsSubscription = this.resultService.segments$.subscribe(segs => {
      this.ngZone.run(() => {
        if (segs.length === 0 && this.segments.length > 0) return;
        this.segments = segs;
        if (segs.length > 0) {
          this.isStreamingMode = true;
          this.isLoading       = false;
        }
        this.cdr.detectChanges();
      });
    });

    this.stepsSubscription = this.resultService.steps$.subscribe(steps => {
      this.ngZone.run(() => {
        if (this.isStreamingComplete && steps.length === 0) {
          this.activeStreamingSteps = [];
          this.cdr.detectChanges();
          return;
        }
        if (steps.length > 0) {
          this._streamDoneTriggered = false;
        }
        this.activeStreamingSteps = steps;
        this.cdr.detectChanges();
      });
    });

    this.resultService.globalSuggestions$.subscribe(suggestions => {
      this.ngZone.run(() => {
        this.globalSuggestions = suggestions || [];
        this.cdr.detectChanges();
      });
    });

    this.completeSubscription = this.resultService.streamingComplete$.subscribe(done => {
      this.ngZone.run(() => {
        this.isStreamingComplete = done;
        this.cdr.detectChanges();
      });
    });

    this.pendingFormsSubscription = this.resultService.pendingForms$.subscribe(forms => {
      this.ngZone.run(() => {
        if (this._formHandledViaRouterState) {
          this.showStayForm   = false;
          this.showFlightForm = false;
          this.cdr.detectChanges();
          return;
        }

        const form = forms[0];
        if (!form) {
          this.showStayForm   = false;
          this.showFlightForm = false;
          this.cdr.detectChanges();
          return;
        }

        this.isStreamingMode = true;
        this.isLoading       = false;

        this.formEntities = form.entities ?? [];
        this.formLanguage = (form as any).language 
        || this.agentService.getLastLanguage() 
        || 'query_fr';
        console.log('📋 formLanguage mis à jour:', this.formLanguage, '| form.language:', (form as any).language);


        if (form.is_merged) {
          this.mixedFormData  = form;
          this.showMixedForm  = true;
          this.showStayForm   = false;
          this.showFlightForm = false;
          this.showTourForm   = false;
          this.activeStreamingSteps = [];
          this.resultService.setStreamingSteps([]);
          this.cdr.detectChanges();
          return;
        }

        const sub = (form.sub_category || '').toLowerCase();
        const isTour = sub.includes('tour') || sub.includes('excursion');
        if (isTour) {
          this.tourFormData   = form;
          this.showTourForm   = true;
          this.showStayForm   = false;
          this.showFlightForm = false;
          this.activeStreamingSteps = [];
          this.resultService.setStreamingSteps([]);
          this.cdr.detectChanges();
          return;
        }

        if (sub.includes('hotel') || sub.includes('stay')) {
          this.stayFormData   = form.form ?? null;
          this.showStayForm   = true;
          this.showFlightForm = false;
          this.activeStreamingSteps = [];
          this.resultService.setStreamingSteps([]);
        } else if (sub.includes('flight') || sub.includes('transport')
                || sub.includes('vol')    || sub.includes('avion')) {
          this.flightFormData = form.form ?? null;
          this.showFlightForm = true;
          this.showStayForm   = false;
          this.activeStreamingSteps = [];
          this.resultService.setStreamingSteps([]);
        }
        this.cdr.detectChanges();
      });
    });

    this.clarificationSubscription = this.resultService.pendingClarification$.subscribe(pc => {
      this.pendingClarification = pc;
    });

    this.resultService.isStreamingMode$.subscribe(mode => {
      this.ngZone.run(() => {
        if (!mode && (this.showStayForm || this.showFlightForm)) return;
        this.isStreamingMode = mode;
        this.cdr.detectChanges();
      });
    });

    this.routeSubscription = this.route.queryParams.subscribe(params => {
      if (params['streaming'] === '1') {
        const hasSegments = this.resultService.getSegments().length > 0;
    if (!hasSegments) {}}
    });
      this._initRestoreFromCache();



    this.crossSellSubscription = this.resultService.crossSellProposals$.subscribe(proposals => {
      this.ngZone.run(() => {
        this.crossSellProposals = proposals;
        this.cdr.detectChanges();
      });
    });

    const initialSuggestions = this.resultService.getSuggestions();
    if (initialSuggestions?.length) {
      this.globalSuggestions = initialSuggestions;
      this.cdr.detectChanges();
    }

    const initialCrossSell = this.resultService.getCrossSellProposals();
    if (initialCrossSell?.length) {
      this.crossSellProposals = initialCrossSell;
      this.cdr.detectChanges();
    }
  }
private async _initRestoreFromCache(): Promise<void> {
  console.log('[Cache] ========= _initRestoreFromCache appelée =========');
  this.isRestoringCache = true;

  const existingSegments = this.resultService.getSegments();
  const hasPendingSteps  = this.resultService.getCurrentSteps()
                               .some((s: any) => s.state === 'pending');

  if (existingSegments.length > 0) {
    this.ngZone.run(() => {
      this.segments            = existingSegments;
      this.isStreamingMode     = true;
      this.isLoading           = false;
      this.isRestoringCache    = false;
      if (!hasPendingSteps) {
        this.isStreamingComplete = true;
        this.resultService.setStreamingComplete();
      }
      this.cdr.detectChanges();
    });
    return;
  }

  const user = this.userService.session();
  const sessionId =
    this.agentService.getSessionId() ||
    (user ? sessionStorage.getItem(`session_${user.user_id}_${user.tenant_id}`) : null);

  if (!sessionId) {
    this.ngZone.run(() => {
      this.isStreamingMode  = false;
      this.isRestoringCache = false;
      this.cdr.detectChanges();
    });
    return;
  }

  const cached = await this.resultsCache.getLastResults(sessionId);

  this.ngZone.run(() => {
    if (cached.length > 0) {
      this.resultService.clearSegments();
      cached.forEach(seg => this.resultService.addSegment(seg));
      this.isStreamingComplete = true;
      this.isStreamingMode     = true;
      this.isLoading           = false;
      this.isRestoringCache    = false;
      this.resultService.setStreamingComplete();
    } else {
      this.isStreamingMode     = false;
      this.isStreamingComplete = true;
      this.isLoading           = false;
      this.isRestoringCache    = false;
    }
    this.cdr.detectChanges();
  });
}
  onMixedFormSubmit(data: any): void {
    this.showMixedForm = false;
    this.mixedFormData = null;
    this.onMixedFormNavigate(data);
  }
openTicketForm(segment: any): void {
  console.log('[TicketForm] segment:', JSON.stringify(segment));
  console.log('[TicketForm] ticketFormOpen avant:', this.ticketFormOpen);
  const key = segment.seg_id;
  this.ticketFormOpen = { ...this.ticketFormOpen, [key]: !this.ticketFormOpen[key] };
  console.log('[TicketForm] ticketFormOpen après:', this.ticketFormOpen);
  console.log('[TicketForm] isTicketFormOpen:', this.isTicketFormOpen(segment));
  this.cdr.detectChanges();
}
isTicketFormOpen(segment: any): boolean {
  return !!this.ticketFormOpen[segment.seg_id];
}
callContact(contact: string | undefined): void {
  const { phones } = this.parseContacts(contact);
  if (phones[0]) window.location.href = `tel:${phones[0]}`;
}
onSegmentAction(action: any): void {
  if (action.url) {
    window.open(action.url, '_blank');
  }
}

parseContacts(contact: string | undefined): { phones: string[], emails: string[] } {
  if (!contact) return { phones: [], emails: [] };
  
  const parts = contact.split('/').map(p => p.trim()).filter(Boolean);
  const phones: string[] = [];
  const emails: string[] = [];

  for (const part of parts) {
    if (part.includes('@')) {
      emails.push(part);
    } else {
      const cleaned = part.replace(/[\s\-\.\(\)]/g, '');
      if (cleaned.length >= 6) phones.push(cleaned);
    }
  }
  return { phones, emails };
}



emailContact(contact: string | undefined): void {
  const { emails } = this.parseContacts(contact);
  if (emails[0]) window.location.href = `mailto:${emails[0]}`;
}
  private _buildFormResponseCallbacks() {
    return {
      onWord: (_word: string) => {},
      onJson: (jsonData: any) => {
        if (jsonData?.pending_segments) {
          this.resultService.setPendingSegmentsFromBackend(jsonData.pending_segments);
        }
      },
      onStatus: (step: string, message: string, subCategory?: string) => {
        this.ngZone.run(() => {
          if (step === 'classifier') {
            this.activeStreamingSteps = [{ id: 'classifier', message: message || 'Analyse...', state: 'pending' }];
          } else if (step === 'classifier_done') {
            this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
              s.id === 'classifier' ? { ...s, state: 'done' as const } : s
            );
          } else if (step === 'agent_start' && subCategory) {
            const normalized = (subCategory.split(':').pop() || subCategory)
              .replace(/\//g, '_')
              .replace(/ /g, '_')
              .replace(/__+/g, '_');

            const exists = this.activeStreamingSteps.find(s =>
              s.id === normalized || s.id === subCategory
            );
            if (!exists) {
              this.activeStreamingSteps = [...this.activeStreamingSteps, {
                id:      normalized,
                label:   subCategory,
                message,
                state:   'pending' as const,
              }];
            }
          }
          this.resultService.setStreamingSteps(this.activeStreamingSteps);
          this.cdr.detectChanges();
        });
      },
      onDone: () => {
        this.isStreamingComplete = true;
        this.cdr.detectChanges();
      },
      onSegmentDone: (event: any) => {
          console.log('[SegmentDone] result_type:', event.result_type, '| show_ticket_form:', event.show_ticket_form);
  console.log('[SegmentDone] event.offers:', event.offers?.length, 
              '| event.results:', event.results?.length,
              '| event.results[0]?.currency:', event.results?.[0]?.currency,
              '| event.offers?.[0]?.currency:', event.offers?.[0]?.currency);
        const seg   = event.seg_id || '';
        const alt1  = seg.replace(/_/g, ' ');
        const alt2  = seg.replace(/_/g, '/');
        const subCat = event.sub_category || '';

        this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
          (s.id === seg || s.id === alt1 || s.id === alt2 || s.id === subCat)
            ? { ...s, state: 'done' as const, count: event.results?.length }
            : s
        );
        this.resultService.setStreamingSteps(this.activeStreamingSteps);

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

        if (event.result_type === 'hotel_search_results' && event.query) {
          this.currentQuery = event.query;
          if (segment.results) {
            segment.results = segment.results.map((h: any) => ({
              ...h,
              _query: event.query
            }));
          }
        }

        this.resultService.addSegment(segment);
        if (event.loyalty_summary) this.resultService.patchLoyalty(event.loyalty_summary);
        this.cdr.detectChanges();
      },
      onSegmentForm: (event: any) => {
          const lang = event.language 
          || this.agentService.getLastLanguage() 
          || 'query_fr';
        this.formLanguage = lang;
        console.log('📋 onSegmentForm | event.language:', event.language);

        this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
          s.id === event.seg_id ? { ...s, state: 'done' as const } : s
        );
        this.resultService.setStreamingSteps(this.activeStreamingSteps);
        this.resultService.addPendingForm({
          seg_id:         event.seg_id,
          sub_category:   event.sub_category,
          sub_categories: event.sub_categories || [],
          is_merged:      event.is_merged || false,
          message:        event.message,
          form:           event.form,
          steps:          event.steps    || [],
          entities:       event.entities || [],
          language:       lang,

        });
      },
      onStreamDone: (event: any) => {
        if (event.loyalty_summary) this.resultService.patchLoyalty(event.loyalty_summary);

        const suggestions = event.suggestions || [];
        if (suggestions.length) {
            this.globalSuggestions = suggestions;
            this.resultService.patchSuggestions(suggestions);
        }

        if (event.cross_sell_proposals?.length) {
          this.resultService.setCrossSellProposals(event.cross_sell_proposals);
        }

        this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
          s.state === 'pending' ? { ...s, state: 'done' as const } : s
        );
        this.resultService.setStreamingSteps(this.activeStreamingSteps);
        this.cdr.detectChanges();

        this.resultService.setStreamingComplete();
        this.isStreamingComplete  = true;
        this._streamDoneTriggered = true;

        setTimeout(() => {
          this.activeStreamingSteps = [];
          this.resultService.setStreamingSteps([]);
          this.cdr.detectChanges();
        }, 1500);
        setTimeout(async () => {
          const sessionId = this.agentService.getSessionId();
          const segments  = this.resultService.getSegments();
          console.log('[Cache] 💾 Tentative sauvegarde | sessionId:', sessionId, '| segments:', segments.length);
          if (sessionId && segments.length > 0) {
            await this.resultsCache.saveResults(sessionId, segments);
            console.log('[Cache] ✅ Sauvegarde effectuée');
          } else {
            console.log('[Cache] ❌ Sauvegarde ignorée — sessionId ou segments manquants');
          }
        }, 500);
      },
      onError: (_err: any) => {
        this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
          s.state === 'pending' ? { ...s, state: 'error' as const } : s
        );
        this.resultService.setStreamingSteps(this.activeStreamingSteps);
        setTimeout(() => {
          this.activeStreamingSteps = [];
          this.resultService.setStreamingSteps([]);
          this.cdr.detectChanges();
        }, 1500);
        this.isStreamingComplete = true;
        this.cdr.detectChanges();
      },
    };
  }
getProgressPercent(sugg: any): number {
  const tierThresholds: Record<string, number> = {
    silver:   500,
    gold:     1500,
    platinum: 5000,
  };
  const nextTier = sugg['next_tier'];
  const needed   = sugg['points_needed'] || 0;
  const threshold = tierThresholds[nextTier] || 1000;
  const spent = threshold - needed;
  return Math.min(100, Math.max(4, Math.round((spent / threshold) * 100)));
}
  private _formHandledViaRouterState = false;

  onFlightFormNavigate(data: any): void {
    this.showFlightForm       = false;
    this.flightFormData       = null;
    this._streamDoneTriggered = false;
    this.isStreamingComplete  = false;
    this.isStreamingMode      = true;
    this.activeStreamingSteps = [];
    this.resultService.setStreamingSteps([]);

    const currentForm = this.resultService.getPendingForms()[0];
    const pendingSegs = {
      ...(this.resultService.getPendingSegmentsFromBackend() || {}),
      more_info_submitted: {
        seg_id:       currentForm?.seg_id       || 'Flight_Booking',
        sub_category: currentForm?.sub_category || 'Flight Booking',
      }
    };

    console.log('🔥 onFlightFormNavigate | pending_segments envoyés:', pendingSegs);

    this.resultService.shiftPendingForm();
    this.agentService.sendFormResponse(
      data.formValues,
      pendingSegs,
      this._buildFormResponseCallbacks(),
      this.formLanguage,
    );
  }

  onStayFormNavigate(data: any): void {
    this.showStayForm         = false;
    this.stayFormData         = null;
    this._streamDoneTriggered = false;
    this.isStreamingComplete  = false;
    this.isStreamingMode      = true;
    this.activeStreamingSteps = [];
    this.resultService.setStreamingSteps([]);

    const currentForm = this.resultService.getPendingForms()[0];
    if (currentForm?.is_merged) {
      this.onMixedFormNavigate(data);
      return;
    }

    const pendingSegs = {
      ...(this.resultService.getPendingSegmentsFromBackend() || {}),
      more_info_submitted: {
        seg_id:         currentForm?.seg_id         || 'Hotel_Booking',
        sub_category:   currentForm?.sub_category   || 'Hotel Booking',
        sub_categories: currentForm?.sub_categories || [],
      }
    };

    this.resultService.shiftPendingForm();
    this.agentService.sendFormResponse(
      data.formValues,
      pendingSegs,
      this._buildFormResponseCallbacks(),
      this.formLanguage,
    );
  }

  onMixedFormNavigate(data: any): void {
    this.showStayForm         = false;
    this.showFlightForm       = false;
    this.showTourForm         = false;
    this.showMixedForm        = false;
    this.stayFormData         = null;
    this.flightFormData       = null;
    this._streamDoneTriggered = false;
    this.isStreamingComplete  = false;
    this.isStreamingMode      = true;
    this.activeStreamingSteps = [];
    this.resultService.setStreamingSteps([]);

    const currentForm = this.resultService.getPendingForms()[0];
    const pendingSegs = {
      ...(this.resultService.getPendingSegmentsFromBackend() || {}),
      more_info_submitted: {
        seg_id:         currentForm?.seg_id         || 'Mixed_Booking',
        sub_category:   currentForm?.sub_category   || 'Mixed Booking',
        sub_categories: currentForm?.sub_categories || [],
      }
    };

    this.resultService.shiftPendingForm();
    this.agentService.sendFormResponse(
      data.formValues,
      pendingSegs,
      this._buildFormResponseCallbacks(),
      this.formLanguage,
    );
  }

  onTourFormNavigate(data: any): void {
    this.showTourForm         = false;
    this.tourFormData         = null;
    this._streamDoneTriggered = false;
    this.isStreamingComplete  = false;
    this.isStreamingMode      = true;
    this.activeStreamingSteps = [];
    this.resultService.setStreamingSteps([]);

    const currentForm = this.resultService.getPendingForms()[0];
    const pendingSegs = {
      ...(this.resultService.getPendingSegmentsFromBackend() || {}),
      more_info_submitted: {
        seg_id:       currentForm?.seg_id       || 'Tour_Excursion_Booking',
        sub_category: currentForm?.sub_category || 'Tour/Excursion Booking',
      }
    };

    this.resultService.shiftPendingForm();
    this.agentService.sendFormResponse(
      data.formValues,
      pendingSegs,
      this._buildFormResponseCallbacks(),
      this.formLanguage,
    );
  }

  onTourFormSubmit(_formResult: any): void {
    this.showTourForm         = false;
    this.tourFormData         = null;
    this.formEntities         = [];
    this._streamDoneTriggered = false;
    this.isStreamingComplete  = false;
  }

  // =========================================================================
  // CROSS-SELL
  // =========================================================================

  onCrossSellClick(proposal: CrossSellProposal): void {
    const currentLang = this.agentService.getLastLanguage();

    this.resultService.clearCrossSellProposals();

    this.resultService.clearSegments();
  
    this._streamDoneTriggered = false;
    this.isStreamingComplete  = false;
    this.isStreamingMode      = true;
    this.activeStreamingSteps = [];
    this.resultService.setStreamingSteps([]);
    this.resultService.resetStreamingComplete();

    const crossSellCtx: CrossSellContext = {
      cross_sell:     true,
      classification: proposal.classification,
      entities:       proposal.entities,
      language:       currentLang,
    };

    this.agentService.sendMessageStream(
      proposal.label,
      this._buildFormResponseCallbacks(),
      crossSellCtx,
    );
  }

  ngOnDestroy(): void {
    this.resultsSubscription?.unsubscribe();
    this.segmentsSubscription?.unsubscribe();
    this.routeSubscription?.unsubscribe();
    this.stepsSubscription?.unsubscribe();
    this.completeSubscription?.unsubscribe();
    this.pendingFormsSubscription?.unsubscribe();
    this.clarificationSubscription?.unsubscribe();
    this.crossSellSubscription?.unsubscribe();
  }

  // =========================================================================
  // TYPE GUARDS — SegmentResult
  // =========================================================================

isSegmentInfo(seg: SegmentResult): boolean {
  return seg.result_type === 'info_results' && !seg.show_ticket_form;
}

isSegmentHuman(seg: SegmentResult): boolean {
  return seg.result_type === 'human_required' || 
         (seg.result_type === 'info_results' && !!seg.show_ticket_form);
}  isSegmentFlight(seg: SegmentResult):      boolean { return seg.result_type === 'flight_search_results'; }
  isSegmentHotel(seg: SegmentResult):       boolean { return seg.result_type === 'hotel_search_results'; }
  isSegmentTransport(seg: SegmentResult):   boolean { return seg.result_type === 'transport_search_results'; }
  isSegmentRestaurant(seg: SegmentResult):  boolean { return seg.result_type === 'restaurant_search_results'; }
  isSegmentActivity(seg: SegmentResult):    boolean { return seg.result_type === 'activity_search_results'; }
  isSegmentSpecialty(seg: SegmentResult):   boolean { return seg.result_type === 'specialty_search_results'; }
  isSegmentError(seg: SegmentResult):       boolean { return seg.result_type === 'error'; }

  getSegmentResults(seg: SegmentResult): any[] {
    return seg.results ?? [];
  }

  // =========================================================================
  // TYPE GUARDS — SearchResults (mode one-shot)
  // =========================================================================

  isFlightResults(results: SearchResults | null): results is { type: 'flight_search_results'; query: any; results: FlightResult[] } {
    return results !== null && results.type === 'flight_search_results';
  }
  isHotelResults(results: SearchResults | null): results is { type: 'hotel_search_results'; query: any; results: HotelResult[] } {
    return results !== null && results.type === 'hotel_search_results';
  }
  isTransportResults(results: SearchResults | null): results is { type: 'transport_search_results'; query: any; results: TransportResult[] } {
    return results !== null && results.type === 'transport_search_results';
  }
  isActivityResults(results: SearchResults | null): results is { type: 'activity_search_results'; query: any; results: ActivityResult[] } {
    return results !== null && results.type === 'activity_search_results';
  }
  isInfoResults(results: SearchResults | null): results is { type: 'info_results'; message: string; sections?: any[] } {
    return results !== null && results.type === 'info_results';
  }
  isRestaurantResults(results: SearchResults | null): results is { type: 'restaurant_search_results'; query: any; results: RestaurantResult[] } {
    return results !== null && results.type === 'restaurant_search_results';
  }
  isSpecialtyResults(results: SearchResults | null): results is { type: 'specialty_search_results'; query: any; results: SpecialtyResult[] } {
    return results !== null && results.type === 'specialty_search_results';
  }
  isMultiSegmentResults(results: SearchResults | null): results is MultiSegmentResults {
    return results !== null && results.type === 'multi_search_results';
  }

  // =========================================================================
  // HELPERS
  // =========================================================================

  get pendingSegments(): boolean {
    if (this.showStayForm || this.showFlightForm || this.showTourForm || this.showMixedForm) return true;
    if (!this.isStreamingMode) return false;
    if (this.isStreamingComplete) return false;
    return true;
  }

  getSectionTitle(seg: SegmentResult | any): string {
    const type = seg?.result_type ?? seg?.type ?? '';
    const titles: Record<string, string> = {
      'flight_search_results':     'Vols disponibles',
      'hotel_search_results':      'Hôtels disponibles',
      'transport_search_results':  'Transports disponibles',
      'restaurant_search_results': 'Restaurants',
      'activity_search_results':   'Activités',
      'specialty_search_results':  'Spécialités',
      'info_results':              'Informations',
      'human_required':            'Assistance conseiller',
      'error':                     'Service indisponible',
      'multi_search_results':      'Résultats complets',
    };
    return titles[type] || 'Résultats';
  }

  getSegmentPillLabel(seg: SegmentResult): string {
    const count = seg.results?.length ?? 0;
    const icons: Record<string, string> = {
      'flight_search_results':     `✈️ ${count} vol(s)`,
      'hotel_search_results':      `🏨 ${count} hôtel(s)`,
      'transport_search_results':  `🚆 ${count} transport(s)`,
      'restaurant_search_results': `🍽️ ${count} restaurant(s)`,
      'activity_search_results':   `🎭 ${count} activité(s)`,
      'specialty_search_results':  `🍴 ${count} spécialité(s)`,
      'info_results':              '💡 Info',
      'human_required':            '👤 Conseiller',
      'error':                     `⚠️ ${seg.label || 'Erreur'}`,
    };
    return icons[seg.result_type] ?? `${count} résultat(s)`;
  }

goBack(): void {
  const sessionId = this.agentService.getSessionId();
  this.router.navigate(['/chat'], {
    queryParams: sessionId ? { session_id: sessionId } : {},
    state: { preserveResults: true }
  });
}

  // =========================================================================
  // CLARIFICATION depuis /results
  // =========================================================================

  onClarificationChoice(subcat: any): void {
    if (!this.pendingClarification || this.isClarificationProcessing) return;

    const pc      = this.pendingClarification;
    const segment = pc.segment || '';

    this.resultService.clearPendingClarification();
    this.isClarificationProcessing = true;

    const processingStep = {
      id:      'clarification_' + Date.now(),
      message: `Recherche : ${subcat.label}...`,
      state:   'pending' as const,
    };
    this.activeStreamingSteps = [...this.activeStreamingSteps, processingStep];

    this.agentService.sendMessageStream(
      subcat.label,
      {
        onWord:  (_word: string) => {},

        onStatus: (step: string, message: string, subCategory?: string) => {
          if (step === 'agent_start' && subCategory) {
            this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
              s.id === processingStep.id ? { ...s, state: 'done' as const } : s
            );
            this.activeStreamingSteps = [
              ...this.activeStreamingSteps,
              { id: subCategory, message, state: 'pending' as const }
            ];
            this.resultService.setStreamingSteps(this.activeStreamingSteps);
          }
        },

        onSegmentDone: (event: any) => {
            console.log('[SegmentDone] event.offers:', event.offers?.length, 
              '| event.results:', event.results?.length,
              '| event.results[0]?.currency:', event.results?.[0]?.currency,
              '| event.offers?.[0]?.currency:', event.offers?.[0]?.currency);
          if (event.result_type === 'management_question') return;
          const alt = (event.seg_id || '').replace(/_/g, ' ');
          this.activeStreamingSteps = this.activeStreamingSteps.map(s =>
            (s.id === event.seg_id || s.id === alt)
              ? { ...s, state: 'done' as const, count: event.results?.length }
              : s
          );
          this.resultService.setStreamingSteps(this.activeStreamingSteps);

          const segment: SegmentResult = {
            seg_id:      event.seg_id,
            result_type: event.result_type,
            message:     event.message     || '',
            follow_up:   event.follow_up   || '',
            suggestions: event.suggestions || [],
            show_ticket_form: event.show_ticket_form || false,
            ...(event.sections ? { sections: event.sections } : {}),
            ...(event.actions  ? { actions:  event.actions  } : {}),
            ...(event.results  ? { results:  event.results  } : {}),
            ...(event.query    ? { query:    event.query    } : {}),
            ...(event.label    ? { label:    event.label    } : {}),
          };
          this.resultService.addSegment(segment);

          if (event.loyalty_summary) {
            this.resultService.patchLoyalty(event.loyalty_summary);
          }
        },

        onSegmentForm: (event: any) => {
          console.log('📋 onSegmentForm | event.language:', event.language);

          this.resultService.addPendingForm({
            seg_id:       event.seg_id,
            sub_category: event.sub_category,
            message:      event.message,
            form:         event.form,
            steps:        event.steps    || [],
            entities:     event.entities || [],
            language:     event.language || this.agentService.getLastLanguage() || 'query_fr',
          });
        },

        onStreamDone: (event: any) => {
          if (event.loyalty_summary) {
            this.resultService.patchLoyalty(event.loyalty_summary);
          }
          if (event.cross_sell_proposals?.length) {
            this.resultService.setCrossSellProposals(event.cross_sell_proposals);
          }
        },

        onJson: (jsonData: any) => {
          if (jsonData.pending_clarification) {
            this.resultService.setPendingClarification(jsonData.pending_clarification);
          }
          if (jsonData.status === 'clarification_needed') {
            this.resultService.setPendingClarification({
              question: jsonData.reply,
              subcats:  jsonData.subcats,
              segment:  jsonData.segment,
              ambiguous_queue: jsonData._meta?.ambiguous_queue || [],
            });
          }
          if (jsonData.loyalty_summary) {
            this.resultService.patchLoyalty(jsonData.loyalty_summary);
          }
        },

        onDone: () => {
          this.isClarificationProcessing = false;
          setTimeout(() => {
            this.activeStreamingSteps = this.activeStreamingSteps.filter(
              s => s.id !== processingStep.id && s.state === 'done'
            );
            this.resultService.setStreamingSteps(this.activeStreamingSteps);
          }, 1500);
        },

        onError: (_error: any) => {
          this.isClarificationProcessing = false;
          this.activeStreamingSteps = this.activeStreamingSteps.filter(
            s => s.id !== processingStep.id
          );
        },
      },
      {
        clarification_pending:  true,
        clarification_subcats:  pc.subcats,
        clarification_segment:  segment,
        clarification_accepted: [],
        clarification_entities: {},
        clarification_choice:   { id: subcat.id, label: subcat.label },
        ambiguous_queue:        pc.ambiguous_queue || [],
      }
    );
  }

  // =========================================================================
  // FORMULAIRES depuis /results
  // =========================================================================

  onStayFormSubmit(formResult: any): void {
    this.showStayForm  = false;
    this.stayFormData  = null;
    this.resultService.shiftPendingForm();
    this.formEntities  = [];
    this.isStreamingComplete = false;

    if (formResult.error) return;

    const rawList = formResult.results || formResult.offers;
    if (!rawList?.length) return;

    const fv = formResult._formValues || {};

    const segment: SegmentResult = {
      seg_id:      'stay_form_' + Date.now(),
      result_type: 'hotel_search_results',
      message:     formResult.reply || '',
      follow_up:   formResult.follow_up || '',
      suggestions: formResult.suggestions || [],
      loyalty_summary: formResult.loyalty_summary,
      query: {
        city: fv.city || '', check_in: fv.check_in || '',
        check_out: fv.check_out || '', guests: fv.guests || 1, rooms: fv.rooms || 1,
      },
      results: rawList.map((hotel: any, i: number) => ({
        id: hotel.id || `hotel_${i}`, type: 'hotel',
        name: hotel.name || 'Hôtel',
        location: { city: hotel.location?.city || fv.city, country: '', address: '' },
        stars: hotel.stars || 0, rating: hotel.rating || 0, rating_count: 0,
        price_per_night: hotel.price_per_night || 0, total_price: 0,
        currency: hotel.currency || 'EUR',
        images: hotel.images || [], amenities: hotel.amenities || [],
        offers: hotel.offers || [], best_offer: hotel.best_offer || null,
        nights: hotel.nights || 0, token: hotel.token || '',
        actions: [{ label: 'Voir détails', action: 'details', url: `/hotel/${hotel.id || i}` }],
      })),
    };
    this.resultService.addSegment(segment);
  }

  onFlightFormSubmit(formResult: any): void {
    this.showFlightForm = false;
    this.flightFormData = null;
    this.resultService.shiftPendingForm();
    this.formEntities   = [];
    this.isStreamingComplete = false;

    if (formResult.error) return;

    const rawList = formResult.results || formResult.offers;
    if (!rawList?.length) return;

    const fv = formResult._formValues || {};

    const segment: SegmentResult = {
      seg_id:      'flight_form_' + Date.now(),
      result_type: 'flight_search_results',
      message:     formResult.reply || '',
      follow_up:   formResult.follow_up || '',
      suggestions: formResult.suggestions || [],
      loyalty_summary: formResult.loyalty_summary,
      query: {
        origin: fv.origin || '', destination: fv.destination || '',
        departure_date: fv.departure_date || '', return_date: fv.return_date || null,
        adults: fv.adults || 1,
      },
      results: rawList.map((flight: any, i: number) => ({
        id: flight.id || `flight_${i}`, type: 'flight',
        airline: flight.airline || 'Compagnie',
        flight_number: flight.flight_number || `FL${i}`,
        stops: flight.stops ?? 0, stop_details: flight.stop_details || null,
        duration: flight.duration || 'N/A',
        departure: { airport: fv.origin, city: fv.origin, time: flight.departure?.time || 'N/A', date: fv.departure_date },
        arrival:   { airport: fv.destination, city: fv.destination, time: flight.arrival?.time   || 'N/A', date: fv.departure_date },
        amenities: flight.amenities || [],
        fare_options: flight.fare_options || [{ name: 'Standard', price: 0, currency: 'EUR', features: [], selected: false, recommended: true }],
      })),
    };
    this.resultService.addSegment(segment);
  }

  // =========================================================================
  // ACTIONS
  // =========================================================================

  onSelectFare(fare: any): void {
    this.router.navigate(['/booking'], { state: { type: 'flight', fare, flight: fare.flight || fare } });
  }

  onViewHotelDetails(hotel: HotelResult): void {
    this.router.navigate(['/hotel', hotel.id], {
      state: {
        hotel: hotel,
        query: this.currentQuery
      }
    });
  }

  onBookHotel(hotel: HotelResult): void {
    this.router.navigate(['/booking'], { state: { hotel } });
  }

  onViewActivityDetails(activityId: string): void {
    this.router.navigate(['/activity', activityId]);
  }

  onFollowUpClick(followUpText: string): void {
    this.router.navigate(['/chat'], { state: { autoSend: followUpText, preserveContext: true } });
  }

  toggleSuggestionDetails(index: number): void {
    this.expandedSuggestionIndex = this.expandedSuggestionIndex === index ? null : index;
  }

  onSuggestionAction(suggestion: Suggestion, action: string, event?: Event): void {
    if (event) event.stopPropagation();
    if (action === 'book') {
      this.router.navigate(['/booking'], { state: { type: suggestion.category || 'general', offer: suggestion, fromSuggestion: true } });
    } else if (suggestion.label) {
      this.router.navigate(['/chat'], { state: { autoSend: suggestion.label, preserveContext: true } });
    }
  }

  showTemporaryNotification(message: string): void {
    const el = document.createElement('div');
    el.className   = 'temporary-notification';
    el.textContent = message;
    document.body.appendChild(el);
    setTimeout(() => {
      el.classList.add('show');
      setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, 2000);
    }, 10);
  }

  formatPrice(price: number, currency = 'EUR'): string {
    const clean = (currency || 'EUR').toUpperCase().replace(/[^A-Z]/g, '').slice(0, 3) || 'EUR';
    try {
      return new Intl.NumberFormat('fr-FR', { style: 'currency', currency: clean }).format(price);
    } catch {
      return `${price.toFixed(2)} ${clean}`;
    }
  }

  getExtraDetails(suggestion: Suggestion): { label: string; value: string }[] {
    const excluded = ['label', 'description', 'badge', 'priority', 'category', 'action',
                      'price', 'currency', 'saving', 'original_price', 'icon', 'type', 'cta', 'title'];
    return Object.entries(suggestion)
      .filter(([key, value]) => !excluded.includes(key) && value !== null && value !== undefined && value !== '')
      .map(([key, value]) => ({ label: key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()), value: String(value) }));
  }
}