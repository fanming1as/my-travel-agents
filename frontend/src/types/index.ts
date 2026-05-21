export interface Location {
  longitude: number;
  latitude: number;
}

export interface Attraction {
  name: string;
  address: string;
  location: Location;
  visit_duration: number;
  description: string;
  category?: string;
  rating?: number;
  photos?: string[];
  image_url?: string;
  ticket_price?: number;
}

export interface Meal {
  type: string;
  name: string;
  address?: string;
  location?: Location;
  description?: string;
  estimated_cost?: number;
}

export interface Hotel {
  name: string;
  address: string;
  location?: Location;
  price_range?: string;
  rating?: string;
  distance?: string;
  type?: string;
  estimated_cost?: number;
}

export interface DayPlan {
  date: string;
  day_index: number;
  description: string;
  transportation: string;
  accommodation: string;
  hotel?: Hotel;
  attractions: Attraction[];
  meals: Meal[];
}

export interface WeatherInfo {
  date: string;
  day_weather: string;
  night_weather: string;
  day_temp: number | string;
  night_temp: number | string;
  wind_direction: string;
  wind_power: string;
}

export interface Budget {
  total_attractions: number;
  total_hotels: number;
  total_meals: number;
  total_transportation: number;
  total: number;
}

export interface TripFormData {
  user_id: string;
  city: string;
  start_date: string;
  end_date: string;
  travel_days: number;
  transportation: string;
  accommodation: string;
  preferences: string[];
  free_text_input: string;
  spending_tier: '经济型' | '舒适型' | '奢侈型';
  budget?: number | null;
}

export interface TripPlan {
  city: string;
  start_date: string;
  end_date: string;
  days: DayPlan[];
  weather_info: WeatherInfo[];
  overall_suggestions: string;
  budget?: Budget;
  exclusive_tips?: string;
}

// --- 新增：Critic 质检评分模型 ---
export interface CriticScore {
  geo_score: number;
  budget_score: number;
  preference_score: number;
  critique: string;
  should_revise?: boolean;
  revision_focus?: string;
}

// --- 修改：扩充响应对象 ---
export interface TripResponse {
  success: boolean;
  message: string;
  session_id?: string;
  user_id?: string;
  data?: TripPlan;
  critic_scores?: CriticScore;
  consumption_tier?: string;
}

export type TripPlanResponse = TripResponse;
