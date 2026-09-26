// stay-search.component.ts
import { Component, OnInit, Input, Output, EventEmitter, OnChanges, SimpleChanges } from '@angular/core';
import { FormBuilder, FormGroup, Validators } from '@angular/forms';
import { ReactiveFormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { AgentService, AgentForm, FormField } from '../../services/agent.service';
import { Router } from '@angular/router';

@Component({
  selector: 'app-stay-search',
  standalone: true,
  templateUrl: './stay-search.component.html',
  imports: [ReactiveFormsModule, CommonModule],
  styleUrls: ['./stay-search.component.scss']
})
export class StaySearchComponent implements OnInit, OnChanges {
  @Input() formData: AgentForm | null = null;
  @Output() submit = new EventEmitter<any>();
  @Output() cancel = new EventEmitter<void>();
  @Input() showModal = false;
  @Input() entities: { type: string; value: string | null }[] = [];
  @Input() originalLanguage: string = 'fr'; 
  @Output() navigateToResults = new EventEmitter<any>();

  searchForm: FormGroup;
  isLoading = false;
  formTitle = 'Recherche d\'hôtels';
  formDescription = 'Veuillez fournir les informations suivantes :';
  dynamicFormFields: FormField[] = [];
  showOptional = false;
  selectedStars: number[] = [];
  // ── Propriétés autocomplete ──
citySuggestions: string[] = [];
showSuggestions = false;
private cityTimeout: any;

  constructor(
    private fb: FormBuilder,
    private agentService: AgentService,
      private router: Router,

  ) {
    this.searchForm = this.fb.group({
      city: ['', Validators.required],
      check_in: ['', Validators.required],
      check_out: ['', Validators.required],
      guests: [1, [Validators.required, Validators.min(1), Validators.max(10)]],
      rooms: [1, [Validators.required, Validators.min(1), Validators.max(5)]],
      price_min: [null],
      price_max: [null],
      stars: [null],
      sort_by: [''],
      order: ['asc']
    });
  }

  ngOnInit(): void {}

  updateGuests(change: number): void {
    const currentValue = this.searchForm.get('guests')?.value || 1;
    const newValue = currentValue + change;
    if (newValue >= 1 && newValue <= 10) {
      this.searchForm.patchValue({ guests: newValue });
    }
  }


onCityInput(event: Event): void {
  const val = (event.target as HTMLInputElement).value.trim();
  clearTimeout(this.cityTimeout);

  if (!val) {
    this.citySuggestions = [];
    this.showSuggestions = false;
    return;
  }

  this.cityTimeout = setTimeout(() => {
    fetch(`https://photon.komoot.io/api/?q=${encodeURIComponent(val)}&limit=6&layer=city`)
      .then(r => r.json())
      .then(data => {
        this.citySuggestions = [...new Set(
          data.features
            ?.filter((f: any) =>
              f.properties.type === 'city' ||
              f.properties.type === 'town'
            )
            ?.map((f: any) => f.properties.name)
        )] as string[];
        this.showSuggestions = this.citySuggestions.length > 0;
      })
      .catch(() => this.citySuggestions = []);
  }, 300);
}

selectCity(city: string): void {
  this.searchForm.patchValue({ city });
  this.showSuggestions = false;
  this.citySuggestions = [];
}

hideSuggestions(): void {
  setTimeout(() => this.showSuggestions = false, 150);
}
  updateRooms(change: number): void {
    const currentValue = this.searchForm.get('rooms')?.value || 1;
    const newValue = currentValue + change;
    if (newValue >= 1 && newValue <= 5) {
      this.searchForm.patchValue({ rooms: newValue });
    }
  }

  toggleOptional(): void {
    this.showOptional = !this.showOptional;
  }

  toggleStar(star: number): void {
    const index = this.selectedStars.indexOf(star);
    if (index === -1) {
      this.selectedStars.push(star);
    } else {
      this.selectedStars.splice(index, 1);
    }
    this.selectedStars.sort((a, b) => a - b);
    this.searchForm.patchValue({ stars: this.selectedStars.join(',') || null });
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['formData'] && this.formData) {
      this.dynamicFormFields = this.formData.fields;
      this.buildFormFromAgent(this.formData);
    } else if (changes['entities'] && this.entities?.length) {
      this._prefillFromEntities();
    }
  }

  private _prefillFromEntities(): void {
    const today    = new Date().toISOString().split('T')[0];
    const nextWeek = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
    const get = (...types: string[]): string | null => {
      for (const type of types) {
        const found = this.entities.find(e => e.type === type && e.value !== null);
        if (found) return found.value as string;
      }
      return null;
    };
const city = get('destination', 'city', 'LOC', 'origin');
    const checkIn  = get('departure_date', 'check_in', 'DATE');
    const checkOut = get('return_date', 'check_out');
const guests = get('guests', 'PERSONS', 'passengers', 'travelers', 'persons');
    const rooms    = get('rooms', 'room_count');
    this.searchForm.patchValue({
      ...(city     ? { city }                             : {}),
      ...(checkIn  ? { check_in: checkIn }                : { check_in: today }),
      ...(checkOut ? { check_out: checkOut }              : {}),
      ...(guests   ? { guests: +guests }                  : {}),
      ...(rooms    ? { rooms: +rooms }                    : {}),
    });
  }

onSubmit(): void {
  if (this.searchForm.valid) {
    this.isLoading = true;
    const formValues = this.searchForm.value;

    this.navigateToResults.emit({
      type:             'hotel_search',
      formValues:       formValues,
      originalLanguage: this.agentService.getLastSegmentFormLanguage()  
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
    const nextWeek = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];

    const getEntity = (...types: string[]): string | null => {
      for (const type of types) {
        const found = this.entities.find(e => e.type === type && e.value !== null);
        if (found) return found.value;
      }
      return null;
    };

const detectedCity = getEntity('destination', 'city', 'LOC', 'origin');
    const detectedCheckIn = getEntity('departure_date', 'check_in', 'date');
    const detectedCheckOut = getEntity('return_date', 'check_out');
const detectedGuests = getEntity('guests', 'PERSONS', 'passengers', 'travelers', 'persons');
    const detectedRooms = getEntity('rooms', 'room_count');

    this.searchForm.patchValue({
      city: detectedCity ?? '',
      check_in: detectedCheckIn ?? '',
      check_out: detectedCheckOut ?? '',
      guests: detectedGuests ? +detectedGuests : 2,
      rooms: detectedRooms ? +detectedRooms : 1
    });
    
    if (agentForm.title) this.formTitle = agentForm.title;
    if (agentForm.description) this.formDescription = agentForm.description;
  }
}