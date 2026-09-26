// services/result.service.ts
import { Injectable } from '@angular/core';
import { BehaviorSubject, ReplaySubject } from 'rxjs';

export interface FlightResult {
  id: string;
  type: 'flight';
  airline: string;
  airline_logo?: string;
  flight_number: string;
  stops: number;
  stop_details: string | {
    airport?: string;
    city?: string;
    date?: string;
    duration?: string;
  } | null;
  duration: string;
  departure: {
    airport: string;
    city: string;
    time: string;
    date: string;
    cabinClass?: string;
  };
  arrival: {
    airport: string;
    city: string;
    time: string;
    date: string;
    cabinClass?: string;
  };
  amenities: Array<{ icon: string; name: string; value: string }>;
  fare_options: Array<{
    name: string;
    price: number;
    currency: string;
    features: string[];
    selected: boolean;
    recommended?: boolean;
  }>;
  price?: { min: number; max: number; currency: string; total: number };
  price_per_night?: number;
  total_price?: number;
  currency?: string;
  cabin_class?: string;
}

export interface HotelResult {
  id: string;
  type: 'hotel';
  name: string;
  location: { city: string; country: string; address: string };
  rating: number;
  rating_count: number;
  price_per_night: number;
  total_price: number;
  currency: string;
  images: string[];
  amenities: Array<{ icon: string; name: string; included: boolean; price?: number }>;
  actions: Array<{ label: string; action: string; url: string }>;
  target_currency?: string;
  offers?: Array<{
    id: string; name: string; board_type: string; board_code: string;
    description: string; price: number; price_per_night: number;
    price_with_markup: number; currency: string; quantity: number;
    adults: number; children: number[]; cancellation_deadline: string;
    stop_reservation: boolean;
  }>;
  best_offer?: any;
  category?: string;
  stars?: number;
  address?: string;
  city?: string;
  image?: string;
  token?: string;
  recommended?: number;
  source?: string;
  price_range?: { min: number; max: number };
  nights?: number;
  _query?: {           
    check_in?:  string;
    check_out?: string;
    guests?:    number;
    city?:      string;
    rooms?:     number;
  };
}

export interface ActivityResult {
  id: string;
  type: 'activity';
  name: string;
  description: string;
  image: string;
  date: string;
  time: string;
  venue: string;
  city: string;
  category: string;
  status: string;
  on_sale: boolean;
  booking_url: string;
  sub_category?: string;
  actions: Array<{ label: string; action: string; url: string }>;
  full_details?: {
    description_full: string; venue_name: string; venue_address: string;
    venue_city: string; venue_info: string; date: string; time: string;
    timezone: string; artists: string[]; promoter: string | null;
    important_notes: string[]; images: string[];
  };
}

export interface TransportResult {
  id: string;
  type: string;
  icon: string;
  name: string;
  color: string;
  company: string;
  duration: string;
  price: number;
  currency: string;
  departure: { time: string; location: string };
  arrival:   { time: string; location: string };
  availability: string;
  actions: Array<{ label: string; action: string; url?: string }>;
}

export interface TransportSearchResults {
  type: 'transport_search_results';
  query: { origin: string; destination: string };
  results: TransportResult[];
  message: string;
  follow_up: string;
  suggestions?: any[];
  loyalty_summary?: any;
}

export interface RestaurantResult {
  id: string;
  type: 'restaurant';
  name: string;
  description?: string;
  categories: Array<{
    name: string; short_name: string;
    icon: string | { prefix: string; suffix: string; url?: string };
  }>;
  location: {
    address: string; locality: string; region: string; postcode: string;
    country: string; formatted_address: string; latitude: number; longitude: number;
  };
  distance: number;
  price?: number | null;
  price_range?: { min: number | null; max: number | null } | null;
  currency?: string;
  rating?: number;
  specialties?: string[];
  services?: string[];
  opening_hours?: {
    open: string; close: string;
    open_eve?: string; close_eve?: string;
  };
  closed_days?: string[];
  contact: {
    tel: string; tel2?: string; email: string; website: string;
  };
  social_media: { facebook_id: string; instagram: string; twitter: string };
  image: string;
  website?: string;
  photos?: Array<{ prefix: string; suffix: string; url?: string }>;
  bestPhoto?: any;
  best_photo?: any;
  actions: Array<{ label: string; action: string; url: string }>;
}

export interface SpecialtyResult {
  id: string;
  type: 'specialty';
  name: string;
  image: string;
  description: string | null;
  origin: string;
  where_to_try: string;
  actions: Array<{ label: string; action: string; url: string }>;
}

export interface InfoResult {
  type: 'info_results';
  message: string;
  sections?: Array<{ title: string; content: string; items?: string[] }>;
  actions?: Array<{ label: string; action: string }>;
  suggestions?: Suggestion[];
  loyalty_summary?: LoyaltySummary;
  follow_up?: string;
}

export interface Suggestion {
  type?: string;
  category?: string;
  label: string;
  title?: string;
  description?: string;
  price?: number;
  currency?: string;
  priority?: 'high' | 'medium' | 'low';
  saving?: number;
  original_price?: number;
  cta?: string;
  icon?: string;
  badge?: string | null;
  action?: string;
  airline?: string;
  flightNumber?: string;
  departure?: string;
  arrival?: string;
  departureTime?: string;
  arrivalTime?: string;
  duration?: string;
  hotelName?: string;
  location?: string;
  stars?: number;
  amenities?: string[];
  activityName?: string;
  includes?: string[];
  restaurantName?: string;
  cuisine?: string;
  address?: string;
  phone?: string;
  hours?: string;
  averagePrice?: string;
  rating?: number;
  [key: string]: any;
}

export interface LoyaltySummary {
  tier: string;
  points_earned: number;
  total_points?: number;
  next_tier?: string;
  points_to_next?: number;
  tier_upgraded?: boolean;
  previous_tier?: string;
  discount?: number;
  points_multiplier?: number;
  benefits?: string[];
}
export interface CrossSellProposal {
  id:             string;
  label:          string;
  description:    string;
  icon:           string;
  badge?:         string | null;
  classification: Array<{ sub_category: string; category: string; confidence: number }>;
  entities:       Array<{ type: string; value: string }>;
}
export interface CabinClassExtension { text: string; }
export interface SearchCriteria      { cabinClass: string; }

export interface BaseSearchResults {
  suggestions?: Suggestion[];
  loyalty_summary?: LoyaltySummary;
  message?: string;
  follow_up?: string;
}

export interface MultiSegmentResults {
  type: 'multi_search_results';
  message: string;
  follow_up: string;
  suggestions?: Suggestion[];
  loyalty_summary?: LoyaltySummary;
  segments: Array<{ type: string; subCategory: string; query: any; results: any[]; count: number }>;
}

export type SearchResults =
  | (BaseSearchResults & { type: 'flight_search_results';     query: any; results: FlightResult[];      cabinClassExtension?: CabinClassExtension; searchCriteria?: SearchCriteria })
  | (BaseSearchResults & { type: 'hotel_search_results';      query: any; results: HotelResult[]      })
  | (BaseSearchResults & { type: 'activity_search_results';   query: any; results: ActivityResult[]   })
  | (BaseSearchResults & { type: 'restaurant_search_results'; query: any; results: RestaurantResult[] })
  | (BaseSearchResults & { type: 'specialty_search_results';  query: any; results: SpecialtyResult[]  })
  | (BaseSearchResults & { type: 'transport_search_results';  query: any; results: TransportResult[]  })
  | InfoResult
  | MultiSegmentResults;




export type SegmentResultType =
  | 'info_results'
  | 'flight_search_results'
  | 'hotel_search_results'
  | 'activity_search_results'
  | 'restaurant_search_results'
  | 'specialty_search_results'
  | 'transport_search_results'
  | 'error'
  | 'human_required'
  | 'management_question';


export interface SegmentResult {
  seg_id:      string;

  result_type: SegmentResultType;

  message:     string;
  follow_up?:  string;
  suggestions?: Suggestion[];

  sections?: Array<{ title: string; content: string; items?: string[] }>;
  actions?:  Array<{ label: string; action: string; contact?: string; url?: string }>;

  results?: any[];
  query?:   any;

  label?: string;   

  loyalty_summary?: LoyaltySummary;
  _showTicketForm?: boolean;
   show_ticket_form?: boolean;
}

export interface SegmentForm {
  seg_id:       string;
  sub_category: string;
  sub_categories?: string[];   
  is_merged?:      boolean; 
  message:      string;
  form?:        any;
  steps?:       any[];
  entities?:    any[];
  pending_segments?: Record<string, any>; 
  language?:    string;

}




@Injectable({ providedIn: 'root' })
export class ResultService {
constructor() {
  this.pendingFormsSubject.next([]);
}

  private resultsSubject = new BehaviorSubject<SearchResults | null>(null);

  private segmentsSubject = new BehaviorSubject<SegmentResult[]>([]);

private _pendingForms: SegmentForm[] = [];
private pendingFormsSubject = new BehaviorSubject<SegmentForm[]>([]);
  private pendingClarificationSubject = new BehaviorSubject<any>(null);

  private loyaltySummarySubject = new BehaviorSubject<LoyaltySummary | null>(null);

  private stepsSubject = new BehaviorSubject<any[]>([]);

  private streamingCompleteSubject = new BehaviorSubject<boolean>(false);
  private isStreamingModeSubject    = new BehaviorSubject<boolean>(false);
  private _pendingSuggestions: Suggestion[] = [];
  private globalSuggestionsSubject = new BehaviorSubject<any[]>([]);
  globalSuggestions$ = this.globalSuggestionsSubject.asObservable();

  private crossSellProposalsSubject = new BehaviorSubject<CrossSellProposal[]>([]);
  crossSellProposals$ = this.crossSellProposalsSubject.asObservable();

  results$            = this.resultsSubject.asObservable();
  segments$           = this.segmentsSubject.asObservable();
  pendingForms$       = this.pendingFormsSubject.asObservable();
  pendingClarification$ = this.pendingClarificationSubject.asObservable();
  loyaltySummary$     = this.loyaltySummarySubject.asObservable();
  steps$              = this.stepsSubject.asObservable();
  streamingComplete$  = this.streamingCompleteSubject.asObservable();
  isStreamingMode$    = this.isStreamingModeSubject.asObservable();


  _pendingSegmentsFromBackend: Record<string, any> = {};

  setPendingSegmentsFromBackend(ps: Record<string, any>): void {
    this._pendingSegmentsFromBackend = ps || {};
    console.log('[ResultService] pending_segments backend stockés:', Object.keys(ps || {}));
  }

  getPendingSegmentsFromBackend(): Record<string, any> {
    return this._pendingSegmentsFromBackend;
  }
  setCrossSellProposals(proposals: CrossSellProposal[]): void {
  this.crossSellProposalsSubject.next(proposals);
  }

  clearCrossSellProposals(): void {
    this.crossSellProposalsSubject.next([]);
  }
  getCrossSellProposals(): CrossSellProposal[] {
    return this.crossSellProposalsSubject.getValue();
  }

  getSuggestions(): Suggestion[] {
    return this.globalSuggestionsSubject.getValue();
  }

  setResults(results: SearchResults): void {
    console.log('[ResultService] setResults (one-shot):', results.type);
    this.resultsSubject.next(results);
  }

  clearResults(): void {
    this.resultsSubject.next(null);
  }

  getCurrentSteps(): any[] {
    return this.stepsSubject.getValue();
  }
 
addSegment(segment: SegmentResult): void {
  if (
    segment.result_type === 'info_results' &&
    !segment.results?.length &&
    !segment.sections?.length &&
    !segment.message?.trim() &&   
    segment.suggestions?.length
  ) {
    this._pendingSuggestions = segment.suggestions ?? [];
    return;
  }

  const current = this.segmentsSubject.getValue();
  this.segmentsSubject.next([...current, segment]);
  this.isStreamingModeSubject.next(true);
  console.log(
    `[ResultService] segment ajouté | seg_id=${segment.seg_id} | ` +
    `type=${segment.result_type} | total=${current.length + 1}`
  );
}
resetStreamingComplete(): void {
  this.streamingCompleteSubject.next(false);
}

  patchLoyalty(loyalty: LoyaltySummary): void {
    const current = this.segmentsSubject.getValue();
    this.segmentsSubject.next(
      current.map(s => ({ ...s, loyalty_summary: loyalty }))
    );
    this.loyaltySummarySubject.next(loyalty);
    console.log(
      `[ResultService] loyalty patchée | tier=${loyalty.tier} | ` +
      `points=${loyalty.points_earned} | segments=${current.length}`
    );
  }
patchSuggestions(suggestions: any[]): void {
  this.globalSuggestionsSubject.next(suggestions); 
  const current = this.segmentsSubject.getValue();
  this.segmentsSubject.next(current.map(s => ({ ...s, suggestions })));
}
  getSegmentsCount(): number {
    return this.segmentsSubject.getValue().length;
  }

  getSegments(): SegmentResult[] {
    return this.segmentsSubject.getValue();
  }

  getPendingFormsCount(): number {
    return this._pendingForms.length;
  }

getPendingForms(): SegmentForm[] {
  return this._pendingForms;
}

  private _emitForms(forms: SegmentForm[]): void {
  this._pendingForms = forms;
  this.pendingFormsSubject.next(forms);
}
addPendingForm(form: SegmentForm): void {
  if (form.is_merged) {
    this._emitForms([form]);
  } else {
    this._emitForms([...this._pendingForms, form]);
  }
  console.log(`[ResultService] segment_form | seg_id=${form.seg_id} | is_merged=${form.is_merged}`);
}

shiftPendingForm(): void {
  this._emitForms(this._pendingForms.slice(1));
}

removePendingForm(seg_id: string): void {
  this._emitForms(this._pendingForms.filter(f => f.seg_id !== seg_id));
}

clearPendingForms(): void {
  this._emitForms([]);
}

setPendingForm(form: any): void {
  if (!form) { this._emitForms([]); return; }
  const normalized: SegmentForm = {
    seg_id:       form.seg_id       || 'legacy',
    sub_category: form.sub_category || '',
    message:      form.message      || '',
    form:         form.form,
    steps:        form.steps        || [],
    entities:     form.entities     || [],
  };
  this._emitForms([normalized]);
}

setPendingForms(forms: any[]): void {
  const normalized: SegmentForm[] = (forms || []).map((f, i) => ({
    seg_id:       f.seg_id       || `legacy_${i}`,
    sub_category: f.sub_category || '',
    message:      f.message      || '',
    form:         f.form,
    steps:        f.steps        || [],
    entities:     f.entities     || [],
  }));
  this._emitForms(normalized);
}


  setPendingClarification(pc: any): void {
    this.pendingClarificationSubject.next(pc);
  }

  clearPendingClarification(): void {
    this.pendingClarificationSubject.next(null);
  }


  setStreamingSteps(steps: any[]): void {
    this.stepsSubject.next(steps);
  }

  setStreamingComplete(): void {
    this.streamingCompleteSubject.next(true);
  }


clearSegments(): void {
  this.segmentsSubject.next([]);

  this.streamingCompleteSubject.next(false);
  this.pendingClarificationSubject.next(null);
  this.loyaltySummarySubject.next(null);
  this.crossSellProposalsSubject.next([]);

  if (this._pendingForms.length === 0) {
    this.isStreamingModeSubject.next(false);
  }
  console.log('[ResultService] segments cleared');
}
  clearAll(): void {
    this._emitForms([]);
    this.resultsSubject.next(null);
    this.segmentsSubject.next([]);
    this.pendingFormsSubject.next([]);
    this.pendingClarificationSubject.next(null);
    this.loyaltySummarySubject.next(null);
    this.stepsSubject.next([]);
    this.streamingCompleteSubject.next(false);
    this.isStreamingModeSubject.next(false);
    this.crossSellProposalsSubject.next([]);
    console.log('[ResultService] full reset');
  }




  patchNarratorMeta(meta: {
    message?: string;
    suggestions?: any[];
    loyalty_summary?: any;
    follow_up?: string;
    segments?: any[];
  }): void {
    console.warn('[ResultService] patchNarratorMeta() est déprécié. Utiliser patchLoyalty().');

    if (meta.loyalty_summary) {
      this.patchLoyalty(meta.loyalty_summary);
    }

    const segs = this.segmentsSubject.getValue();
    if (segs.length === 0) return;

    const patched = segs.map(s => ({
      ...s,
      ...(meta.message    ? { message:     meta.message    } : {}),
      ...(meta.follow_up  ? { follow_up:   meta.follow_up  } : {}),
      ...(meta.suggestions ? { suggestions: meta.suggestions } : {}),
    }));
    this.segmentsSubject.next(patched);
  }
}