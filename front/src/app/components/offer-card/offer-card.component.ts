import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import { OfferItem, OfferService } from '../../services/offer.service';

@Component({
  selector: 'app-offer-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './offer-card.component.html',
  styleUrls: ['./offer-card.component.scss']
})
export class OfferCardComponent {
  @Input() offer!: OfferItem;
  @Output() viewDetail = new EventEmitter<OfferItem>();
  @Output() book       = new EventEmitter<OfferItem>();
  @Input() isBooking  = false; 
@Input() isBooked   = false
@Input() hasError = false;
@Input() alreadyExisted = false;


  constructor(public offerService: OfferService) {}
  imageError = false;
  onViewDetail(): void {
    this.viewDetail.emit(this.offer);
  }

  onBook(): void {
    this.book.emit(this.offer);
  }


get imageUrl(): string {
  if (this.imageError || !this.offer.image_url) {
    return 'https://images.unsplash.com/photo-1488646953014-85cb44e25828?w=400&q=80';
  }
  return this.offer.image_url;
}

onImageError(): void {
  this.imageError = true;
}
}