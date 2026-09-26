// mixed-search/mixed-search.component.ts
import {
  Component, Input, Output, EventEmitter,
  OnInit, OnChanges, SimpleChanges,
} from '@angular/core';
import { FormBuilder, FormGroup, Validators, ReactiveFormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { AgentService } from '../../services/agent.service';
import { SegmentForm } from '../../services/result.service';
import { Router } from '@angular/router';

@Component({
  selector:    'app-mixed-search',
  standalone:  true,
  imports:     [ReactiveFormsModule, CommonModule],
  templateUrl: './mixed-search.component.html',
  styleUrls:   ['./mixed-search.component.scss'],
})
export class MixedSearchComponent implements OnInit, OnChanges {

  @Input()  segmentForm!: SegmentForm;
  @Input()  entities: { type: string; value: string | null }[] = [];
  @Input()  originalLanguage = 'fr';
  @Input() showModal = false;

originSuggestions:      string[] = [];
destinationSuggestions: string[] = [];
citySuggestions:        string[] = [];
tourCitySuggestions:    string[] = [];

showOriginSug      = false;
showDestinationSug = false;
showCitySug        = false;
showTourCitySug    = false;

private originTimeout:      any;
private destinationTimeout: any;
private cityTimeout:        any;
private tourCityTimeout:    any;
  @Output() navigateToResults = new EventEmitter<any>();
  @Output() cancel = new EventEmitter<void>();

  hasFlight = false;
  hasHotel  = false;
  hasTour   = false;

  form!: FormGroup;
  isLoading = false;

  readonly cabinClasses = ['ECONOMY', 'PREMIUM_ECONOMY', 'BUSINESS', 'FIRST'];

  constructor(
    private fb:FormBuilder,
    private router: Router,
    private agentService: AgentService,
  ) {}


  ngOnInit(): void {
    this._detectAgentTypes();
    this._buildForm();
    this._prefillFromEntities();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['segmentForm'] && !changes['segmentForm'].firstChange) {
      this._detectAgentTypes();
      if (this.form) this._prefillFromEntities();
    }
    if (changes['entities'] && !changes['entities'].firstChange && this.form) {
      this._prefillFromEntities();
    }
  }


  get formTitle(): string {
    const parts: string[] = [];
    if (this.hasFlight) parts.push('vol');
    if (this.hasHotel)  parts.push('hôtel');
    if (this.hasTour)   parts.push('activités');
    if (parts.length === 0) return 'Complétez votre recherche';
    if (parts.length === 1) return `Votre ${parts[0]}`;
    const last = parts.pop();
    return `Votre ${parts.join(', ')} et ${last}`;
  }

  get sectionIcons(): string {
    const icons: string[] = [];
    if (this.hasFlight) icons.push('✈️');
    if (this.hasHotel)  icons.push('🏨');
    if (this.hasTour)   icons.push('🗺️');
    return icons.join(' ');
  }

private fetchCities(val: string, callback: (cities: string[]) => void): void {
  fetch(`https://photon.komoot.io/api/?q=${encodeURIComponent(val)}&limit=6&layer=city`)
    .then(r => r.json())
    .then(data => {
      const cities = [...new Set(
        data.features
          ?.filter((f: any) =>
            f.properties.type === 'city' ||
            f.properties.type === 'town'
          )
          ?.map((f: any) => f.properties.name)
      )] as string[];
      callback(cities);
    })
    .catch(() => callback([]));
}

onCityFieldInput(event: Event, field: 'origin' | 'destination' | 'city' | 'tour_city'): void {
  const val = (event.target as HTMLInputElement).value.trim();
  const timeoutMap: any = {
    origin: 'originTimeout', destination: 'destinationTimeout',
    city: 'cityTimeout', tour_city: 'tourCityTimeout'
  };
  const showMap: any = {
    origin: 'showOriginSug', destination: 'showDestinationSug',
    city: 'showCitySug', tour_city: 'showTourCitySug'
  };
  const sugMap: any = {
    origin: 'originSuggestions', destination: 'destinationSuggestions',
    city: 'citySuggestions', tour_city: 'tourCitySuggestions'
  };

  clearTimeout((this as any)[timeoutMap[field]]);

  if (val.length < 2) {
    (this as any)[sugMap[field]] = [];
    (this as any)[showMap[field]] = false;
    return;
  }

  (this as any)[timeoutMap[field]] = setTimeout(() => {
    this.fetchCities(val, cities => {
      (this as any)[sugMap[field]] = cities;
      (this as any)[showMap[field]] = cities.length > 0;
    });
  }, 300);
}

selectCityField(city: string, field: 'origin' | 'destination' | 'city' | 'tour_city'): void {
  this.form.patchValue({ [field]: city });
  const showMap: any = {
    origin: 'showOriginSug', destination: 'showDestinationSug',
    city: 'showCitySug', tour_city: 'showTourCitySug'
  };
  (this as any)[showMap[field]] = false;
}

hideCityField(field: 'origin' | 'destination' | 'city' | 'tour_city'): void {
  const showMap: any = {
    origin: 'showOriginSug', destination: 'showDestinationSug',
    city: 'showCitySug', tour_city: 'showTourCitySug'
  };
  setTimeout(() => (this as any)[showMap[field]] = false, 150);
}
  private _detectAgentTypes(): void {
    const subs: string[] = this.segmentForm?.sub_categories ?? [];
    const form  = (this.segmentForm?.form as any) ?? {};
    const types = form?.agent_types ?? {};

    if (Object.keys(types).length) {
      this.hasFlight = !!types['has_flight'];
      this.hasHotel  = !!types['has_hotel'];
      this.hasTour   = !!types['has_tour'];
    } else {
      this.hasFlight = subs.some(s => /flight|vol|transport/i.test(s));
      this.hasHotel  = subs.some(s => /hotel|stay|hébergement/i.test(s));
      this.hasTour   = subs.some(s => /tour|excursion|activity/i.test(s));
    }

    if (!this.hasFlight && !this.hasHotel && !this.hasTour) {
      this.hasHotel = true;
    }
  }


  private _buildForm(): void {
    const req = Validators.required;

    this.form = this.fb.group({
      origin:         ['', this.hasFlight ? req : []],
      destination:    ['', this.hasFlight ? req : []],
      departure_date: ['', this.hasFlight ? req : []],
      return_date:    [''],
      adults:         [1, [Validators.min(1), Validators.max(10)]],
      cabin_class:    ['ECONOMY'],

      city:      ['', this.hasHotel ? req : []],
      check_in:  ['', this.hasHotel ? req : []],
      check_out: ['', this.hasHotel ? req : []],
      guests:    [1, [Validators.min(1), Validators.max(10)]],
      rooms:     [1, [Validators.min(1), Validators.max(5)]],

      tour_city:           ['', this.hasTour && !this.hasHotel ? req : []],
      tour_departure_date: [''],
    });
  }


private _prefillFromEntities(): void {
  const get = (...types: string[]): string | null => {
    for (const t of types) {
      const e = this.entities.find(x => x.type === t && x.value !== null);
      if (e) return e.value as string;
    }
    return null;
  };

  const origin      = get('origin');
  const destination = get('destination');
  const departure   = get('departure_date', 'DATE');
  const returnDate  = get('return_date');
  const checkIn     = get('check_in') ?? departure ?? '';   // ← séparé
  const checkOut    = get('check_out') ?? '';               // ← séparé, pas return_date
  const adults      = get('adults', 'guests', 'PERSONS');
  const city        = get('city', 'LOC');
  const tourCity    = get('tour_city', 'city', 'LOC');
  const cabin       = get('cabin_class', 'MISC');

  this.form.patchValue({
    origin:         origin      ?? '',
    destination:    destination ?? '',
    departure_date: departure   ?? '',
    return_date:    returnDate  ?? '',
    adults:         adults ? +adults : 1,
    cabin_class:    cabin?.toUpperCase() ?? 'ECONOMY',

    city:      city ?? '',
    check_in:  checkIn,    // ← check_in séparé
    check_out: checkOut,   // ← check_out séparé, pas return_date
    guests:    adults ? +adults : 1,

    tour_city:           tourCity  ?? '',
    tour_departure_date: departure ?? '',
  });
}

  adjustCount(field: string, delta: number, min = 1, max = 10): void {
    const ctrl = this.form.get(field);
    if (!ctrl) return;
    const next = (ctrl.value ?? min) + delta;
    if (next >= min && next <= max) ctrl.setValue(next);
  }


  isValid(): boolean { return this.form.valid; }

  onSubmit(): void {
    if (!this.isValid()) {
      this.form.markAllAsTouched();
      return;
    }
    this.isLoading = true;
    const fv = this.form.value;
    const formData: Record<string, any> = {};

    if (this.hasFlight) {
      formData['origin']         = fv.origin;
      formData['destination']    = fv.destination;
      formData['departure_date'] = fv.departure_date;
      if (fv.return_date) formData['return_date'] = fv.return_date;
      formData['adults']         = fv.adults;
      formData['cabin_class']    = fv.cabin_class;
    }

    if (this.hasHotel) {
    formData['city']        = fv.city;
    formData['hotel_city']  = fv.city;  
    formData['check_in']    = fv.check_in;
    formData['check_out']   = fv.check_out;
    formData['guests']      = fv.guests;
    formData['rooms']       = fv.rooms;
    formData['stars']       = null;  
    formData['cabin_class'] = null;     
    }

    if (this.hasTour) {

  if (this.hasHotel) {
    const tourCity = fv.tour_city?.trim();
    if (tourCity && tourCity !== fv.city?.trim()) {
      formData['tour_city'] = tourCity;  
    }
  } else {
    formData['city'] = fv.tour_city?.trim();
  }
  if (fv.tour_departure_date) {
    formData['tour_departure_date'] = fv.tour_departure_date;
  }
}

    if (this.originalLanguage) {
      this.agentService.setLastLanguage(this.originalLanguage);
    }

    this.navigateToResults.emit({
      type:             'mixed_search',
      formValues:       formData,
      subCategories:    this.segmentForm?.sub_categories ?? [],
      originalLanguage: this.agentService.getLastLanguage(),
    });

    this.isLoading = false;
  }

onCancel(): void {
  const sessionId = this.agentService.getSessionId();
  this.router.navigate(['/chat'], {
    queryParams: sessionId ? { session_id: sessionId } : {}
  });
}

  hasError(field: string): boolean {
    const ctrl = this.form.get(field);
    return !!(ctrl?.invalid && ctrl?.touched);
  }

  get adultsVal(): number { return this.form.get('adults')?.value  ?? 1; }
  get guestsVal(): number { return this.form.get('guests')?.value  ?? 1; }
  get roomsVal():  number { return this.form.get('rooms')?.value   ?? 1; }
}