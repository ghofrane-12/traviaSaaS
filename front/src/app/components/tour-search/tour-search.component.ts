import { Component, Input, Output, EventEmitter, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ResultService, SegmentForm } from '../../services/result.service';
import { AgentService } from '../../services/agent.service';
import { Router } from '@angular/router';

@Component({
  selector: 'app-tour-search',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './tour-search.component.html',
  styleUrl: './tour-search.component.scss'
})
export class TourSearchComponent implements OnInit {

  @Input() showModal        = false;
  @Input() formData:        SegmentForm | null = null;
  @Input() entities:        { type: string; value: string | null }[] = [];
  @Input() originalLanguage = 'fr';

  @Output() navigateToResults = new EventEmitter<any>();
  @Output() cancel            = new EventEmitter<void>();

  internalForm   = { city: '', departure_date: '' };
  submitted_flag = false;
citySuggestions: string[] = [];
showSuggestions = false;
private cityTimeout: any;
  today = new Date().toISOString().split('T')[0];

  constructor(private resultService: ResultService,
     private router: Router,
  private agentService: AgentService,
  ) {}

  ngOnInit(): void {
    for (const e of this.entities) {
      if (e.type === 'LOC'  && !this.internalForm.city)
        this.internalForm.city = e.value ?? '';
      if (e.type === 'DATE' && !this.internalForm.departure_date)
        this.internalForm.departure_date = e.value ?? '';
    }
  }
onCancel(): void {
  if (this.formData?.seg_id) {
    this.resultService.removePendingForm(this.formData.seg_id);
  }
  const sessionId = this.agentService.getSessionId();
  this.router.navigate(['/chat'], {
    queryParams: sessionId ? { session_id: sessionId } : {}
  });
}
  onSubmit(): void {
    this.submitted_flag = true;
    if (!this.internalForm.city.trim()) return;

    this.navigateToResults.emit({
      type:             'tour_search',
      formValues: {
        city:           this.internalForm.city.trim(),
        departure_date: this.internalForm.departure_date || undefined,
      },
       originalLanguage: this.agentService.getLastSegmentFormLanguage(),
    });
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
  this.internalForm.city = city;
  this.showSuggestions = false;
  this.citySuggestions = [];
}

hideSuggestions(): void {
  setTimeout(() => this.showSuggestions = false, 150);
}
}