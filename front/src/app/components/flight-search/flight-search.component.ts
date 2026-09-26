// transport-search.component.ts
import { Component, OnInit, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { FormBuilder, FormGroup, ValidationErrors, Validators } from '@angular/forms';
import { ReactiveFormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { AgentService, AgentForm, FormField } from '../../services/agent.service';
import { Router } from '@angular/router';

@Component({
  selector: 'app-flight-search',
  standalone: true,
  templateUrl: './flight-search.component.html',
  imports: [ReactiveFormsModule, CommonModule],
  styleUrls: ['./flight-search.component.scss']
})
export class FlightSearchComponent implements OnInit, OnChanges {
  @Input() formData: AgentForm | null = null;
  @Output() submit = new EventEmitter<any>();
  @Output() cancel = new EventEmitter<void>();
  @Output() navigateToResults = new EventEmitter<any>();
  @Input() showModal = false;
  @Input() entities: { type: string; value: string | null }[] = [];
  @Input() originalLanguage: string = 'fr';  
  searchForm: FormGroup;
  isLoading = false;
  formTitle = 'Recherche de vols';
  originSuggestions: string[] = [];
  showOriginSuggestions = false;
  private originTimeout: any;
  destinationSuggestions: string[] = [];
  showDestinationSuggestions = false;
  private destinationTimeout: any;
  formDescription = 'Veuillez fournir les informations suivantes :';
  dynamicFormFields: FormField[] = [];
  showOptional = false;

  cabinClassOptions = ['ECONOMY', 'PREMIUM_ECONOMY', 'BUSINESS', 'FIRST'];

  constructor(
    private fb: FormBuilder,
    private router: Router,
    private agentService: AgentService
  ) {
    this.searchForm = this.fb.group({
      origin: ['', Validators.required],
      destination: ['', Validators.required],
      departure_date: ['', Validators.required],
      return_date: [''],
      adults: [1, [Validators.required, Validators.min(1), Validators.max(10)]],
      cabin_class: ['ECONOMY'],
      price_min: [null, [Validators.min(0)]],
      price_max: [null, [Validators.min(0)]]
    }, { validators: this.priceRangeValidator });
  }
  priceRangeValidator(group: FormGroup): ValidationErrors | null {
    const min = group.get('price_min')?.value;
    const max = group.get('price_max')?.value;
    if (min !== null && max !== null && min !== '' && max !== '' && Number(min) > Number(max)) {
      return { priceRangeInvalid: true };
    }
    return null;
  }

  ngOnInit(): void {}

  updateAdults(change: number): void {
    const currentValue = this.searchForm.get('adults')?.value || 1;
    const newValue = currentValue + change;
    if (newValue >= 1 && newValue <= 10) {
      this.searchForm.patchValue({ adults: newValue });
    }
  }

  toggleOptional(): void {
    this.showOptional = !this.showOptional;
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['formData'] && this.formData) {
      this.dynamicFormFields = this.formData.fields;
      this.formTitle = this.formData.title;
      this.formDescription = this.formData.description;
      this.buildFormFromAgent(this.formData);
    } else if (changes['entities'] && this.entities?.length) {
      this._prefillFromEntities();
    }
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

onOriginInput(event: Event): void {
  const val = (event.target as HTMLInputElement).value.trim();
  clearTimeout(this.originTimeout);
  if (val.length < 2) { this.originSuggestions = []; this.showOriginSuggestions = false; return; }
  this.originTimeout = setTimeout(() => {
    this.fetchCities(val, cities => {
      this.originSuggestions = cities;
      this.showOriginSuggestions = cities.length > 0;
    });
  }, 300);
}

onDestinationInput(event: Event): void {
  const val = (event.target as HTMLInputElement).value.trim();
  clearTimeout(this.destinationTimeout);
  if (val.length < 2) { this.destinationSuggestions = []; this.showDestinationSuggestions = false; return; }
  this.destinationTimeout = setTimeout(() => {
    this.fetchCities(val, cities => {
      this.destinationSuggestions = cities;
      this.showDestinationSuggestions = cities.length > 0;
    });
  }, 300);
}

selectOrigin(city: string): void {
  this.searchForm.patchValue({ origin: city });
  this.showOriginSuggestions = false;
}

selectDestination(city: string): void {
  this.searchForm.patchValue({ destination: city });
  this.showDestinationSuggestions = false;
}

hideOriginSuggestions(): void {
  setTimeout(() => this.showOriginSuggestions = false, 150);
}

hideDestinationSuggestions(): void {
  setTimeout(() => this.showDestinationSuggestions = false, 150);
}
private _prefillFromEntities(): void {
  const today = new Date().toISOString().split('T')[0];

  const locs = this.entities
    .filter(e => e.type === 'LOC' && e.value !== null)
    .map(e => e.value as string);

  const get = (...types: string[]): string | null => {
    for (const type of types) {
      const found = this.entities.find(e => e.type === type && e.value !== null);
      if (found) return found.value as string;
    }
    return null;
  };

  const origin      = get('origin', 'origine', 'departure') ?? null;
  const destination = get('destination', 'arrival')          ?? locs[0] ?? null;

  const departure  = get('departure_date', 'DATE');
  const returnDate = get('return_date', 'RETURN_DATE');
const adults = get('adults', 'PERSONS', 'passengers', 'travelers', 'persons');
  const cabin      = get('cabin_class', 'class');

  this.searchForm.patchValue({
    ...(origin      ? { origin }                           : {}),
    ...(destination ? { destination }                      : {}),
    ...(departure   ? { departure_date: departure }        : {}),
    ...(returnDate  ? { return_date: returnDate }          : {}),
    ...(adults      ? { adults: +adults }                  : {}),
    ...(cabin       ? { cabin_class: cabin.toUpperCase() } : {}),
  });
}

onSubmit(): void {
  if (this.searchForm.valid) {
    this.isLoading = true;
    const formValues = this.searchForm.value;

    if (this.originalLanguage) {
      this.agentService.setLastLanguage(this.originalLanguage);
    }

    this.navigateToResults.emit({
      type: 'flight_search',
      formValues,
      originalLanguage: this.agentService.getLastSegmentFormLanguage(),
    });

    this.isLoading = false;
  }
}
onCancel(): void {
  const sessionId = this.agentService.getSessionId();
  this.router.navigate(['/chat'], {
    queryParams: sessionId ? { session_id: sessionId } : {}
  });
}

private buildFormFromAgent(agentForm: AgentForm): void {
  const today = new Date().toISOString().split('T')[0];

  const locs = this.entities
    .filter(e => e.type === 'LOC' && e.value !== null)
    .map(e => e.value as string);

  const getEntity = (...types: string[]): string | null => {
    for (const type of types) {
      const found = this.entities.find(e => e.type === type && e.value !== null);
      if (found) return found.value;
    }
    return null;
  };

  const detectedOrigin      = getEntity('origin', 'origine', 'departure') ?? null;
  const detectedDestination = getEntity('destination', 'arrival')          ?? locs[0] ?? null;
  const detectedDeparture   = getEntity('departure_date', 'DATE');
const detectedReturnDate = getEntity('return_date', 'RETURN_DATE');
const detectedAdults = getEntity('adults', 'PERSONS', 'passengers', 'travelers', 'persons');
  const detectedCabin       = getEntity('cabin_class', 'class');

  this.searchForm.patchValue({
    origin:         detectedOrigin      ?? '',
    destination:    detectedDestination ?? '',
    departure_date: detectedDeparture   ?? '',
    return_date:    detectedReturnDate  ?? null,
    adults:         detectedAdults ? +detectedAdults : 1,
    cabin_class:    detectedCabin?.toUpperCase() ?? 'ECONOMY',
  });

  if (agentForm.title)       this.formTitle       = agentForm.title;
  if (agentForm.description) this.formDescription = agentForm.description;
}
}