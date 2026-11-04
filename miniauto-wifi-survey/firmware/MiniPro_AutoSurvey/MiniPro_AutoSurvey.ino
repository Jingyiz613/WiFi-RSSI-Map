#include "FactoryControl.h"
#include "AutoMotion.h"
#include "WifiSurvey.h"
#include "SurveyAuto.h"

void setup() {
  factoryControlSetup();
  wifiSurveySetup();
  surveyAutoSetup();
}

void loop() {
  factoryControlUpdate();
  autoMotionUpdate();
  wifiSurveyUpdate();
  surveyAutoUpdate();
}
